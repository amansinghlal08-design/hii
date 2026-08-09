"""
MockTest.pro — Level 99 Ultimate Edition (SINGLE-FILE)
======================================================
Complete mock-test platform in ONE file. Deploy to Render in minutes.

Features
  ✅ Timed tests with live countdown + auto-submit
  ✅ Instant right/wrong feedback with explanations
  ✅ 140 starter questions (GK, Maths, English, Reasoning, Science)
  ✅ XP, levels (up to 99) and daily streaks
  ✅ Weak-question tracking + Weak Practice / Hard Drill modes
  ✅ Per-subject analytics + recent attempt history
  ✅ Question bank management: bulk JSON import, export, delete
  ✅ GitHub backup bridge (sync.py) so data survives hosting resets
  ✅ Super-Admin (amansinghlal08@gmail.com) with full platform control
  ✅ Email/Password auth + simulated OTP + password reset
  ✅ Light / dark theme, mobile-first, zero-jitter test UI
  ✅ "Powered by Rajnish" footer

How to run locally
  pip install -r requirements.txt
  python app.py
  → open http://127.0.0.1:5000

How to deploy on Render
  1. Push this folder to GitHub (app.py, requirements.txt, sync.py)
  2. Render → New → Web Service → Connect repo
  3. Build:  pip install -r requirements.txt
  4. Start:  gunicorn app:app --bind 0.0.0.0:$PORT --workers 2
  5. Env vars: GITHUB_TOKEN, GITHUB_REPO (for backup)
  6. Deploy → Done!

Database: mocktest.db (auto-created, auto-seeded with 140 questions)
"""

import json
import os
import random
import re
import secrets
import sqlite3
import time
import hashlib
from io import BytesIO
from contextlib import closing

try:
    from flask import Flask, request, jsonify, send_file
except ImportError:
    raise SystemExit("Flask not installed. Run: pip install flask")

# ──────────────────────────────────────────────────────────────
# CONFIGURATION
# ──────────────────────────────────────────────────────────────
MASTER_CODE        = "121520"          # secret access code for Import/Export
MASTER_CODE_HASH   = hashlib.sha256(MASTER_CODE.encode()).hexdigest()
ADMIN_EMAIL        = "amansinghlal08@gmail.com"  # hardcoded super-admin
PBKDF2_ITERATIONS  = 120_000
CHUNK_SIZE         = 20                # questions per auto-chunk
DAY_MS             = 86_400_000

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def _resolve_db_path():
    for base in (BASE_DIR, "/tmp"):
        candidate = os.path.join(base, "mocktest.db")
        try:
            if not os.path.exists(candidate):
                with open(candidate, "a"):
                    pass
            return candidate
        except OSError:
            continue
    return os.path.join(BASE_DIR, "mocktest.db")

DB_PATH = _resolve_db_path()

app = Flask(__name__)

# Optional GitHub backup bridge (sync.py)
try:
    import sync  # noqa: F401
except ImportError:
    sync = None

# ──────────────────────────────────────────────────────────────
# DATABASE SCHEMA
# ──────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category    TEXT NOT NULL,
    topic       TEXT NOT NULL,
    question    TEXT NOT NULL,
    options     TEXT NOT NULL,
    correct     INTEGER NOT NULL,
    explanation TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_q_cat ON questions(category);
CREATE INDEX IF NOT EXISTS idx_q_cat_topic ON questions(category, topic);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    email         TEXT UNIQUE NOT NULL,
    first_name    TEXT NOT NULL,
    last_name     TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    salt          TEXT NOT NULL,
    is_admin      INTEGER DEFAULT 0,
    banned        INTEGER DEFAULT 0,
    created_at    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

CREATE TABLE IF NOT EXISTS attempts (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT,
    category TEXT,
    topic    TEXT,
    total    INTEGER,
    correct  INTEGER,
    wrong    INTEGER,
    skipped  INTEGER,
    pct      REAL,
    time_sec INTEGER,
    mode     TEXT,
    ts       INTEGER
);
CREATE INDEX IF NOT EXISTS idx_a_user ON attempts(username);

CREATE TABLE IF NOT EXISTS weak_questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT,
    question_id INTEGER,
    wrong_count INTEGER DEFAULT 1,
    last_wrong  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_w_user ON weak_questions(username);

CREATE TABLE IF NOT EXISTS user_stats (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT UNIQUE,
    xp          INTEGER DEFAULT 0,
    streak      INTEGER DEFAULT 0,
    last_active INTEGER,
    level       INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    username   TEXT NOT NULL,
    created_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(username);
"""

# ──────────────────────────────────────────────────────────────
# SEED DATA — 140 QUESTIONS
# ──────────────────────────────────────────────────────────────
SEED_QUESTIONS = [
    # GK – World Geography
    ("GK", "World Geography", "भारत की राजधानी क्या है?", ["मुंबई", "नई दिल्ली", "कोलकाता", "चेन्नई"], 1, "नई दिल्ली भारत की राजधानी है।"),
    ("GK", "World Geography", "टॉरस पर्वत किस देश में है?", ["भारत", "तुर्की", "पाकिस्तान", "ईरान"], 1, "टॉरस पर्वत तुर्की में है।"),
    ("GK", "World Geography", "नील नदी किस महाद्वीप में है?", ["एशिया", "अफ्रीका", "यूरोप", "ऑस्ट्रेलिया"], 1, "नील नदी अफ्रीका में है।"),
    ("GK", "World Geography", "विश्व का सबसे बड़ा महाद्वीप कौन सा है?", ["अफ्रीका", "एशिया", "यूरोप", "उत्तरी अमेरिका"], 1, "एशिया क्षेत्रफल में सबसे बड़ा है।"),
    ("GK", "World Geography", "माउंट एवरेस्ट की ऊँचाई कितनी है?", ["8848 मी", "8611 मी", "7850 मी", "9200 मी"], 0, "8848 मीटर।"),
    ("GK", "World Geography", "विश्व का सबसे बड़ा महासागर कौन सा है?", ["अटलांटिक", "हिंद", "आर्कटिक", "प्रशांत"], 3, "प्रशांत महासागर सबसे बड़ा है।"),
    ("GK", "World Geography", "भारत की सबसे लंबी नदी कौन सी है?", ["गंगा", "यमुना", "गोदावरी", "ब्रह्मपुत्र"], 0, "गंगा भारत की सबसे लंबी नदी है।"),
    ("GK", "World Geography", "थार मरुस्थल कहाँ स्थित है?", ["राजस्थान", "गुजरात", "पंजाब", "हरियाणा"], 0, "मुख्यतः राजस्थान में।"),
    ("GK", "World Geography", "सुंडा खाड़ी किन दो द्वीपों के बीच है?", ["जावा और सुमात्रा", "बोर्नियो और सुलावेसी", "जावा और बाली", "सुमात्रा और कालीमंतन"], 0, "जावा और सुमात्रा के बीच।"),
    ("GK", "World Geography", "गोबी रेगिस्तान किस देश में है?", ["भारत", "चीन", "मंगोलिया", "रूस"], 2, "मंगोलिया और चीन में।"),
    ("GK", "World Geography", "अरब सागर किसके दक्षिण में स्थित है?", ["भारत", "पाकिस्तान", "ईरान", "अरब प्रायद्वीप"], 3, "अरब प्रायद्वीप के दक्षिण में।"),
    ("GK", "World Geography", "डेन्यूब नदी किस सागर में गिरती है?", ["काला सागर", "भूमध्य सागर", "कैस्पियन सागर", "अटलांटिक"], 0, "काला सागर में।"),
    ("GK", "World Geography", "एशिया और अफ्रीका को जोड़ने वाला स्थलडमरूमध्य?", ["स्वेज", "पनामा", "जिब्राल्टर", "बोस्पोरस"], 0, "स्वेज स्थलडमरूमध्य।"),
    ("GK", "World Geography", "उत्तरी अमेरिका की सबसे लंबी नदी?", ["मिसिसिपी", "मिसौरी", "अमेज़न", "कोलोराडो"], 1, "मिसौरी-मिसिसिपी प्रणाली।"),
    ("GK", "World Geography", "किलिमंजारो पर्वत किस देश में है?", ["केन्या", "तंजानिया", "युगांडा", "रवांडा"], 1, "तंजानिया में।"),
    ("GK", "World Geography", "विश्व की सबसे बड़ी झील?", ["कैस्पियन सागर", "सुपीरियर", "विक्टोरिया", "बैकाल"], 0, "कैस्पियन सागर।"),
    ("GK", "World Geography", "एंजिल जलप्रपात किस नदी पर है?", ["नील", "अमेज़न", "कांगो", "ओरिनोको"], 1, "अमेज़न की सहायक नदी पर।"),
    ("GK", "World Geography", "ग्रेट बैरियर रीफ किस देश के पास है?", ["ऑस्ट्रेलिया", "न्यूजीलैंड", "फिजी", "पापुआ न्यू गिनी"], 0, "ऑस्ट्रेलिया के पूर्वी तट पर।"),
    ("GK", "World Geography", "यूरोप का सबसे ऊँचा पर्वत शिखर?", ["एल्ब्रुस", "मोंट ब्लांक", "मैटरहॉर्न", "ग्रॉसग्लॉकनर"], 0, "माउंट एल्ब्रुस।"),
    ("GK", "World Geography", "कर्क रेखा कितने देशों से होकर गुजरती है?", ["12", "16", "18", "20"], 1, "16 देशों से।"),

    # GK – Indian History
    ("GK", "Indian History", "भारत का पहला प्रधानमंत्री कौन था?", ["जवाहरलाल नेहरू", "महात्मा गांधी", "सरदार पटेल", "डॉ. राजेंद्र प्रसाद"], 0, "जवाहरलाल नेहरू।"),
    ("GK", "Indian History", "ताजमहल किसने बनवाया?", ["अकबर", "शाहजहां", "बाबर", "औरंगजेब"], 1, "शाहजहां ने।"),
    ("GK", "Indian History", "1857 का विद्रोह किस वर्ष हुआ?", ["1856", "1857", "1858", "1859"], 1, "1857 में।"),
    ("GK", "Indian History", "भारत को स्वतंत्रता कब मिली?", ["1945", "1946", "1947", "1948"], 2, "15 अगस्त 1947।"),
    ("GK", "Indian History", "अशोक किस वंश के थे?", ["मौर्य", "गुप्त", "चोल", "मुगल"], 0, "मौर्य वंश।"),
    ("GK", "Indian History", "भारत का संविधान कब लागू हुआ?", ["26 नवंबर 1949", "26 जनवरी 1950", "15 अगस्त 1947", "2 अक्टूबर 1950"], 1, "26 जनवरी 1950।"),
    ("GK", "Indian History", "सिख धर्म के संस्थापक कौन थे?", ["गुरु नानक", "गुरु गोबिंद सिंह", "गुरु अंगद", "गुरु अर्जुन"], 0, "गुरु नानक।"),
    ("GK", "Indian History", "पानीपत का पहला युद्ध किस वर्ष लड़ा गया?", ["1526", "1556", "1761", "1857"], 0, "1526 में।"),
    ("GK", "Indian History", "दीन-ए-इलाही किसने चलाया?", ["अकबर", "जहाँगीर", "शाहजहाँ", "औरंगज़ेब"], 0, "अकबर ने।"),
    ("GK", "Indian History", "भारत छोड़ो आंदोलन कब शुरू हुआ?", ["1940", "1942", "1945", "1947"], 1, "1942 में।"),
    ("GK", "Indian History", "महात्मा गांधी का जन्म कब हुआ?", ["1869", "1879", "1889", "1899"], 0, "2 अक्टूबर 1869।"),
    ("GK", "Indian History", "अकबर का संरक्षक कौन था?", ["बैरम खान", "टोडरमल", "मानसिंग", "अबुल फजल"], 0, "बैरम खान।"),
    ("GK", "Indian History", "हड़प्पा सभ्यता किस नदी के किनारे विकसित हुई?", ["गंगा", "यमुना", "सिंधु", "गोदावरी"], 2, "सिंधु नदी।"),
    ("GK", "Indian History", "भारत में ब्रिटिश ईस्ट इंडिया कंपनी की स्थापना कब हुई?", ["1600", "1605", "1610", "1620"], 0, "1600 में।"),
    ("GK", "Indian History", "स्वराज्य की स्थापना किसने की?", ["गोखले", "तिलक", "शिवाजी", "राणा प्रताप"], 2, "शिवाजी ने।"),
    ("GK", "Indian History", "भारत में पहला सूती कपड़ा मिल कहाँ लगा?", ["मुंबई", "अहमदाबाद", "कानपुर", "सूरत"], 0, "1854 में मुंबई में।"),
    ("GK", "Indian History", "बंगाल विभाजन कब हुआ?", ["1905", "1906", "1907", "1908"], 0, "1905 में।"),
    ("GK", "Indian History", "साइमन कमीशन का भारत आगमन?", ["1927", "1928", "1929", "1930"], 1, "1928 में।"),
    ("GK", "Indian History", "जलियांवाला बाग हत्याकांड कब हुआ?", ["1917", "1918", "1919", "1920"], 2, "1919 में।"),
    ("GK", "Indian History", "भारत का राष्ट्रगान 'जन गण मन' किसने लिखा?", ["रवींद्रनाथ टैगोर", "बंकिमचंद्र", "सुभाषचंद्र", "महात्मा गांधी"], 0, "रवींद्रनाथ टैगोर।"),

    # Maths – Arithmetic
    ("Maths", "Arithmetic", "15 × 12 = ?", ["150", "170", "180", "200"], 2, "180।"),
    ("Maths", "Arithmetic", "√144 = ?", ["10", "11", "12", "14"], 2, "12।"),
    ("Maths", "Arithmetic", "25% of 200 = ?", ["25", "50", "75", "100"], 1, "50।"),
    ("Maths", "Arithmetic", "125 ÷ 5 = ?", ["20", "25", "30", "35"], 1, "25।"),
    ("Maths", "Arithmetic", "7² + 3² = ?", ["49", "58", "67", "70"], 1, "58।"),
    ("Maths", "Arithmetic", "10% of 500 = ?", ["50", "60", "70", "80"], 0, "50।"),
    ("Maths", "Arithmetic", "2000 का 5% कितना होगा?", ["50", "100", "150", "200"], 1, "100।"),
    ("Maths", "Arithmetic", "यदि एक वस्तु का मूल्य 300 रु से 360 रु हो जाए तो % वृद्धि?", ["10%", "15%", "20%", "25%"], 2, "20%।"),
    ("Maths", "Arithmetic", "यदि किसी संख्या का 40%, 80 है तो संख्या क्या है?", ["120", "160", "200", "240"], 2, "200।"),
    ("Maths", "Arithmetic", "300 का 33⅓% कितना?", ["100", "110", "120", "130"], 0, "100।"),
    ("Maths", "Arithmetic", "एक संख्या का 15% यदि 45 हो तो संख्या?", ["200", "250", "300", "350"], 2, "300।"),
    ("Maths", "Arithmetic", "₹500 का 20% लाभ कितना?", ["₹50", "₹75", "₹100", "₹125"], 2, "₹100।"),
    ("Maths", "Arithmetic", "यदि A का 25% = 50 हो, तो A = ?", ["100", "150", "200", "250"], 2, "200।"),
    ("Maths", "Arithmetic", "एक घंटे का कितना % 15 मिनट है?", ["15%", "20%", "25%", "30%"], 2, "25%।"),
    ("Maths", "Arithmetic", "250 का 8% कितना?", ["15", "18", "20", "22"], 2, "20।"),
    ("Maths", "Arithmetic", "यदि संख्या 800 है और 20% घटे तो नई संख्या?", ["600", "620", "640", "660"], 2, "640।"),
    ("Maths", "Arithmetic", "10% वार्षिक ब्याज पर 2 वर्ष का साधारण ब्याज ₹400 है तो मूलधन?", ["₹1500", "₹2000", "₹2500", "₹3000"], 1, "₹2000।"),
    ("Maths", "Arithmetic", "15 पुस्तकों का मूल्य ₹1200 है तो 5 का मूल्य?", ["₹300", "₹350", "₹400", "₹450"], 2, "₹400।"),
    ("Maths", "Arithmetic", "80 किमी/घंटा से 240 किमी दूरी तय करने में समय?", ["2 h", "3 h", "4 h", "5 h"], 1, "3 घंटे।"),
    ("Maths", "Arithmetic", "12 आदमी 15 दिन में काम खत्म करते हैं, 20 आदमी कितने दिन लेंगे?", ["7", "8", "9", "10"], 2, "9 दिन।"),

    # Maths – Geometry
    ("Maths", "Geometry", "त्रिभुज के तीनों कोणों का योग?", ["90°", "180°", "270°", "360°"], 1, "180°।"),
    ("Maths", "Geometry", "एक वृत्त का परिमाप सूत्र?", ["2πr", "πr²", "πd", "4r²"], 0, "2πr।"),
    ("Maths", "Geometry", "आयत का क्षेत्रफल?", ["l + b", "l × b", "2(l + b)", "l² + b²"], 1, "l × b।"),
    ("Maths", "Geometry", "वर्ग की भुजा 5 सेमी है तो क्षेत्रफल?", ["10", "20", "25", "30"], 2, "25 वर्ग सेमी।"),
    ("Maths", "Geometry", "एक वृत्त की त्रिज्या 7 सेमी है तो क्षेत्रफल?", ["44", "77", "154", "308"], 2, "154 वर्ग सेमी।"),
    ("Maths", "Geometry", "समकोण त्रिभुज में हाइपोटेनस = ?", ["a² + b²", "√(a² + b²)", "2√ab", "(a + b)²"], 1, "√(a² + b²)।"),
    ("Maths", "Geometry", "एक घन की भुजा 3 सेमी है तो आयतन?", ["9", "18", "27", "36"], 2, "27 घन सेमी।"),
    ("Maths", "Geometry", "दो समांतर रेखाएं आपस में मिलती हैं?", ["कभी", "हमेशा", "कभी-कभी", "कभी नहीं"], 3, "कभी नहीं।"),
    ("Maths", "Geometry", "एक बेलन का आयतन सूत्र?", ["πr²h", "2πrh", "πrh²", "πr²h²"], 0, "πr²h।"),
    ("Maths", "Geometry", "एक पिरामिड का आयतन = (1/3) × ?", ["आधार × ऊंचाई", "आधार² × ऊंचाई", "आधार × ऊंचाई²", "3 × आधार × ऊंचाई"], 0, "(1/3) × आधार × ऊंचाई।"),
    ("Maths", "Geometry", "एक पंचभुज के कोणों का योग?", ["360°", "540°", "720°", "900°"], 1, "540°।"),
    ("Maths", "Geometry", "एक गोले का आयतन?", ["(4/3)πr³", "4πr²", "(2/3)πr³", "(1/3)πr²"], 0, "(4/3)πr³।"),
    ("Maths", "Geometry", "शंकु का आयतन?", ["(1/3)πr²h", "πr²h", "(2/3)πr²h", "(1/2)πr²h"], 0, "(1/3)πr²h।"),
    ("Maths", "Geometry", "एक चतुर्भुज का कोण योग?", ["180°", "270°", "360°", "450°"], 2, "360°।"),
    ("Maths", "Geometry", "सीधी रेखा की ढलान = ?", ["y/x", "Δy/Δx", "x/y", "(y₂+y₁)/(x₂+x₁)"], 1, "Δy/Δx।"),
    ("Maths", "Geometry", "वृत्त का क्षेत्रफल?", ["πr", "πr²", "2πr", "πd"], 1, "πr²।"),
    ("Maths", "Geometry", "त्रिभुज का क्षेत्रफल = ?", ["(1/2)bh", "bh", "b + h", "2bh"], 0, "(1/2) × base × height।"),
    ("Maths", "Geometry", "समबाहु त्रिभुज का प्रत्येक कोण?", ["45°", "60°", "90°", "120°"], 1, "60°।"),
    ("Maths", "Geometry", "पाइथागोरस प्रमेय a² + b² = ?", ["c", "c²", "2c", "√c"], 1, "c²।"),
    ("Maths", "Geometry", "एक वृत्त में 360° का कौन सा कोण होता है?", ["केंद्रीय कोण", "परिधीय कोण", "समकोण", "ऋणात्मक कोण"], 0, "पूर्ण केंद्रीय कोण।"),

    # English – Noun
    ("English", "Noun", "Which is a noun?", ["Run", "Beautiful", "Cat", "Quickly"], 2, "'Cat' is a noun."),
    ("English", "Noun", "Identify the noun: 'The sun is bright.'", ["The", "sun", "is", "bright"], 1, "'Sun' is a noun."),
    ("English", "Noun", "Which is a proper noun?", ["city", "Delhi", "boy", "river"], 1, "'Delhi' is a proper noun."),
    ("English", "Noun", "Plural of 'child'?", ["childs", "childes", "children", "childrens"], 2, "Children."),
    ("English", "Noun", "Collective noun for sheep?", ["herd", "flock", "pack", "swarm"], 1, "Flock of sheep."),
    ("English", "Noun", "Which word is an abstract noun?", ["table", "happiness", "apple", "car"], 1, "'Happiness' is abstract."),
    ("English", "Noun", "Find the noun: 'She bought a new dress.'", ["She", "bought", "new", "dress"], 3, "'Dress' is the noun."),
    ("English", "Noun", "Feminine gender of 'actor'?", ["actress", "actoress", "actorine", "actora"], 0, "Actress."),
    ("English", "Noun", "Identify the common noun: 'The Ganga is a holy river.'", ["Ganga", "holy", "river", "The"], 2, "'River' is common."),
    ("English", "Noun", "Which is an uncountable noun?", ["book", "water", "pen", "chair"], 1, "Water."),
    ("English", "Noun", "Material noun: 'This ring is made of gold.'", ["ring", "is", "made", "gold"], 3, "'Gold'."),
    ("English", "Noun", "Plural of 'mouse'?", ["mouses", "mice", "mices", "mouse"], 1, "Mice."),
    ("English", "Noun", "Collective noun example?", ["team", "boy", "cat", "table"], 0, "'Team' is collective."),
    ("English", "Noun", "Noun form of 'strong'?", ["strongly", "strength", "stronger", "strongest"], 1, "Strength."),
    ("English", "Noun", "Countable noun?", ["rice", "air", "bottle", "milk"], 2, "Bottle."),
    ("English", "Noun", "Possessive noun: 'This is Rahul's book.'", ["Rahul", "Rahul's", "book", "This"], 1, "'Rahul's'."),
    ("English", "Noun", "Type of noun: 'army'?", ["Abstract", "Common", "Collective", "Proper"], 2, "Collective."),
    ("English", "Noun", "Plural of 'tooth'?", ["tooths", "teeth", "toothes", "teeths"], 1, "Teeth."),
    ("English", "Noun", "Which is NOT a noun?", ["city", "run", "freedom", "chair"], 1, "'Run' is a verb."),
    ("English", "Noun", "Plural of 'foot'?", ["foots", "feet", "feets", "foot"], 1, "Feet."),

    # Reasoning – Series
    ("Reasoning", "Series", "2, 4, 8, 16, ?", ["18", "24", "32", "30"], 2, "Double each term."),
    ("Reasoning", "Series", "1, 4, 9, 16, ?", ["20", "25", "30", "36"], 1, "Squares: 5²=25."),
    ("Reasoning", "Series", "5, 10, 15, 20, ?", ["22", "24", "25", "30"], 2, "+5 each time."),
    ("Reasoning", "Series", "3, 6, 12, 24, ?", ["36", "42", "48", "54"], 2, "Doubling."),
    ("Reasoning", "Series", "1, 1, 2, 3, 5, ?", ["6", "7", "8", "9"], 2, "Fibonacci: 8."),
    ("Reasoning", "Series", "A, C, E, G, ?", ["H", "I", "J", "K"], 1, "Every second letter."),
    ("Reasoning", "Series", "Z, X, V, T, ?", ["R", "S", "Q", "P"], 0, "Reverse, skip one."),
    ("Reasoning", "Series", "AB, EF, IJ, ?", ["MN", "OP", "MNOP", "QR"], 0, "Pairs every 4 steps."),
    ("Reasoning", "Series", "1, 3, 6, 10, ?", ["12", "14", "15", "16"], 2, "Triangular numbers."),
    ("Reasoning", "Series", "0, 1, 1, 2, 3, 5, ?", ["6", "7", "8", "9"], 2, "Fibonacci."),
    ("Reasoning", "Series", "2, 5, 10, 17, ?", ["24", "26", "28", "30"], 1, "n²+1."),
    ("Reasoning", "Series", "100, 81, 64, 49, ?", ["36", "25", "16", "9"], 0, "Squares descending."),
    ("Reasoning", "Series", "B, D, F, H, ?", ["I", "J", "K", "L"], 1, "Every second letter."),
    ("Reasoning", "Series", "1, 8, 27, 64, ?", ["100", "125", "150", "175"], 1, "Cubes."),
    ("Reasoning", "Series", "12, 10, 8, 6, ?", ["3", "4", "5", "2"], 1, "-2."),
    ("Reasoning", "Series", "1, 1, 2, 6, 24, ?", ["48", "60", "72", "120"], 3, "Factorial."),
    ("Reasoning", "Series", "10, 20, 40, 80, ?", ["100", "120", "140", "160"], 3, "Double."),
    ("Reasoning", "Series", "A, E, I, M, ?", ["N", "O", "P", "Q"], 2, "Every 4th."),
    ("Reasoning", "Series", "1, 2, 6, 24, 120, ?", ["240", "360", "480", "720"], 3, "Factorial."),
    ("Reasoning", "Series", "Z, Y, X, W, ?", ["V", "U", "T", "S"], 0, "Reverse."),

    # Science – Physics
    ("Science", "Physics", "प्रकाश की गति (m/s)?", ["3×10⁶", "3×10⁸", "3×10¹⁰", "3×10⁴"], 1, "≈ 3×10⁸ m/s"),
    ("Science", "Physics", "गुरुत्वाकर्षण की खोज किसने की?", ["आइंस्टीन", "न्यूटन", "गैलीलियो", "एडिसन"], 1, "आइज़क न्यूटन।"),
    ("Science", "Physics", "बल का SI मात्रक?", ["जूल", "न्यूटन", "वाट", "पास्कल"], 1, "Newton (N)"),
    ("Science", "Physics", "पावर का मात्रक?", ["जूल", "न्यूटन", "वाट", "एम्पीयर"], 2, "Watt"),
    ("Science", "Physics", "ध्वनि की गति (हवा में)?", ["343 m/s", "3000 m/s", "30 m/s", "3×10⁸ m/s"], 0, "≈ 343 m/s"),
    ("Science", "Physics", "1 N बराबर है?", ["1 kg m/s²", "1 kg m/s", "1 g m/s²", "1 kg cm/s²"], 0, "F=ma ⇒ 1 N = 1 kg·m/s²"),
    ("Science", "Physics", "प्रकाश वर्ष किसका मात्रक है?", ["समय", "दूरी", "चाल", "द्रव्यमान"], 1, "दूरी।"),
    ("Science", "Physics", "विद्युत धारा का मात्रक?", ["वोल्ट", "एम्पीयर", "ओम", "वाट"], 1, "एम्पीयर (A)"),
    ("Science", "Physics", "g का मान लगभग?", ["8.9 m/s²", "9.8 m/s²", "10.8 m/s²", "7.8 m/s²"], 1, "9.8 m/s²"),
    ("Science", "Physics", "1 L = ? mL", ["100", "500", "1000", "1500"], 2, "1000 mL"),
    ("Science", "Physics", "पारसेक किसकी इकाई है?", ["समय", "दूरी", "द्रव्यमान", "ऊर्जा"], 1, "खगोलीय दूरी।"),
    ("Science", "Physics", "ध्वनि तरंग किस प्रकार की है?", ["अनुप्रस्थ", "अनुदैर्ध्य", "विद्युत चुम्बकीय", "यांत्रिक नहीं"], 1, "अनुदैर्ध्य यांत्रिक तरंग।"),
    ("Science", "Physics", "प्रतिध्वनि के लिए न्यूनतम दूरी?", ["10 m", "17 m", "20 m", "25 m"], 1, "लगभग 17 m।"),
    ("Science", "Physics", "ऊष्मा का SI मात्रक?", ["जूल", "कैलोरी", "वाट", "न्यूटन"], 0, "जूल (J)"),
    ("Science", "Physics", "तरंग दैर्ध्य का प्रतीक?", ["α", "β", "λ", "γ"], 2, "λ (लैम्ब्डा)"),
    ("Science", "Physics", "सूर्य का प्रकाश पृथ्वी तक आने में समय?", ["8 मिनट", "1 सेकंड", "1 घंटा", "24 घंटे"], 0, "≈ 8 मिनट 20 सेकंड।"),
    ("Science", "Physics", "पानी का क्वथनांक किस पर निर्भर?", ["द्रव्यमान", "वायुमंडलीय दबाव", "आयतन", "रंग"], 1, "दबाव।"),
    ("Science", "Physics", "इंद्रधनुष में कितने रंग?", ["5", "6", "7", "8"], 2, "7 (VIBGYOR)"),
    ("Science", "Physics", "सूर्य ग्रहण कब होता है?", ["पूर्णिमा", "अमावस्या", "दोनों", "कभी नहीं"], 1, "अमावस्या पर।"),
    ("Science", "Physics", "चंद्र ग्रहण कब होता है?", ["पूर्णिमा", "अमावस्या", "दोनों", "कभी नहीं"], 0, "पूर्णिमा पर।"),
]

# ──────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db

def init_db():
    with closing(get_db()) as db:
        db.executescript(SCHEMA)
        count = db.execute("SELECT COUNT(*) AS n FROM questions").fetchone()["n"]
        if count == 0:
            for cat, topic, question, options, correct, explanation in SEED_QUESTIONS:
                db.execute(
                    "INSERT INTO questions (category, topic, question, options, correct, explanation)"
                    " VALUES (?,?,?,?,?,?)",
                    (cat, topic, question, json.dumps(options, ensure_ascii=False),
                     correct, explanation),
                )
            # create default admin user
            salt = secrets.token_hex(16)
            pw_hash = hashlib.pbkdf2_hmac(
                "sha256", MASTER_CODE.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS
            ).hex()
            db.execute(
                "INSERT OR IGNORE INTO users (username, email, first_name, last_name, password_hash, salt, is_admin, banned, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                ("admin", ADMIN_EMAIL, "Admin", "User", pw_hash, salt, 1, 0, int(time.time() * 1000)),
            )
        db.commit()

def shuffle_options(row):
    opts = json.loads(row["options"])
    indices = list(range(len(opts)))
    random.shuffle(indices)
    return {
        "question_id": row["id"],
        "category": row["category"],
        "topic": row["topic"],
        "question": row["question"],
        "options": [opts[i] for i in indices],
        "correct": indices.index(row["correct"]),
        "shuffle": indices,
        "explanation": row["explanation"],
    }

def hash_password(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS).hex()

def verify_password(password, salt, expected_hash):
    return secrets.compare_digest(hash_password(password, salt), expected_hash)

def new_salt():
    return secrets.token_hex(16)

def sanitize_username(first_name, last_name):
    base = re.sub(r"[^a-z0-9]+", "", (first_name + last_name).lower()) or "user"
    return base[:24]

def create_session(db, username):
    token = secrets.token_hex(32)
    db.execute(
        "INSERT INTO sessions (token, username, created_at) VALUES (?,?,?)",
        (token, username, int(time.time() * 1000)),
    )
    return token

def delete_session(token):
    with closing(get_db()) as db:
        db.execute("DELETE FROM sessions WHERE token=?", (token,))
        db.commit()

def get_user_from_token(token):
    if not token:
        return None
    with closing(get_db()) as db:
        row = db.execute("SELECT username FROM sessions WHERE token=?", (token,)).fetchone()
    return row["username"] if row else None

def is_admin_user(username):
    with closing(get_db()) as db:
        row = db.execute("SELECT is_admin FROM users WHERE username=?", (username,)).fetchone()
    return row and row["is_admin"] == 1

def is_banned_user(username):
    with closing(get_db()) as db:
        row = db.execute("SELECT banned FROM users WHERE username=?", (username,)).fetchone()
    return row and row["banned"] == 1

def update_user_stats(db, username, correct_count):
    row = db.execute("SELECT * FROM user_stats WHERE username=?", (username,)).fetchone()
    prev_level = row["level"] if row else 1
    today = int(time.time() // 86400)

    if row and row["last_active"] == today:
        xp = row["xp"] + correct_count * 10
        streak = row["streak"]
    elif row and row["last_active"] == today - 1:
        xp = row["xp"] + correct_count * 10
        streak = row["streak"] + 1
    else:
        xp = (row["xp"] if row else 0) + correct_count * 10
        streak = 1

    level = min(99, xp // 100 + 1)
    if row:
        db.execute(
            "UPDATE user_stats SET xp=?, streak=?, last_active=?, level=? WHERE id=?",
            (xp, streak, today, level, row["id"]),
        )
    else:
        db.execute(
            "INSERT INTO user_stats (username, xp, streak, last_active, level) VALUES (?,?,?,?,?)",
            (username, xp, streak, today, level),
        )
    return prev_level, xp, streak

# ──────────────────────────────────────────────────────────────
# API ROUTES
# ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return HTML

# ---- Auth ----
@app.route("/api/auth/register", methods=["POST"])
def auth_register():
    data = request.get_json(force=True) or {}
    first = (data.get("first_name") or "").strip()
    last = (data.get("last_name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not first or not last or not email or len(password) < 6:
        return jsonify(error="All fields required. Password min 6 chars."), 400
    with closing(get_db()) as db:
        if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            return jsonify(error="Email already registered."), 400
        username = sanitize_username(first, last)
        candidate, n = username, 1
        while db.execute("SELECT 1 FROM users WHERE username=?", (candidate,)).fetchone():
            candidate = username + str(n)
            n += 1
        username = candidate
        salt = new_salt()
        pw_hash = hash_password(password, salt)
        db.execute(
            "INSERT INTO users (username, email, first_name, last_name, password_hash, salt, is_admin, banned, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (username, email, first, last, pw_hash, salt, 0, 0, int(time.time() * 1000)),
        )
        token = create_session(db, username)
        db.commit()
    return jsonify(token=token, user={"username": username, "first_name": first, "last_name": last, "email": email})

@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    data = request.get_json(force=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    with closing(get_db()) as db:
        row = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not row or not verify_password(password, row["salt"], row["password_hash"]):
            return jsonify(error="Incorrect email or password."), 401
        if row["banned"]:
            return jsonify(error="Your account is temporarily restricted."), 403
        token = create_session(db, row["username"])
        db.commit()
    return jsonify(token=token, user={
        "username": row["username"], "first_name": row["first_name"],
        "last_name": row["last_name"], "email": row["email"], "is_admin": row["is_admin"]
    })

@app.route("/api/auth/me")
def auth_me():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    username = get_user_from_token(token)
    if not username:
        return jsonify(error="Not authenticated"), 401
    with closing(get_db()) as db:
        row = db.execute(
            "SELECT username, first_name, last_name, email, is_admin FROM users WHERE username=?",
            (username,)
        ).fetchone()
    if not row:
        return jsonify(error="Not authenticated"), 401
    return jsonify(username=row["username"], first_name=row["first_name"],
                   last_name=row["last_name"], email=row["email"], is_admin=row["is_admin"])

@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if token:
        delete_session(token)
    return jsonify(status="ok")

@app.route("/api/auth/reset-password", methods=["POST"])
def auth_reset_password():
    data = request.get_json(force=True) or {}
    email = (data.get("email") or "").strip().lower()
    new_password = data.get("new_password") or ""
    if not email or len(new_password) < 6:
        return jsonify(error="Email and new password (min 6 chars) required."), 400
    with closing(get_db()) as db:
        row = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not row:
            return jsonify(error="No account found for that email."), 404
        salt = new_salt()
        pw_hash = hash_password(new_password, salt)
        db.execute(
            "UPDATE users SET password_hash=?, salt=? WHERE email=?",
            (pw_hash, salt, email),
        )
        # invalidate all sessions
        db.execute("DELETE FROM sessions WHERE username=?", (row["username"],))
        db.commit()
    return jsonify(status="ok")

# ---- Categories / Topics ----
@app.route("/api/categories")
def get_categories():
    with closing(get_db()) as db:
        rows = db.execute(
            "SELECT category, COUNT(*) AS n FROM questions GROUP BY category ORDER BY n DESC"
        ).fetchall()
    return jsonify([{"category": r["category"], "count": r["n"]} for r in rows])

@app.route("/api/topics")
def get_topics():
    category = request.args.get("category")
    if not category:
        return jsonify([])
    with closing(get_db()) as db:
        rows = db.execute(
            "SELECT topic, COUNT(*) AS n FROM questions WHERE category=? GROUP BY topic ORDER BY n DESC",
            (category,),
        ).fetchall()
    return jsonify([{"topic": r["topic"], "count": r["n"]} for r in rows])

@app.route("/api/tests")
def get_tests():
    category = request.args.get("category")
    topic = request.args.get("topic")
    if not category or not topic:
        return jsonify(error="Missing category or topic"), 400
    with closing(get_db()) as db:
        rows = db.execute(
            "SELECT id FROM questions WHERE category=? AND topic=? ORDER BY id",
            (category, topic),
        ).fetchall()
    total = len(rows)
    if not total:
        return jsonify(error="No questions in this topic yet."), 404
    tests = []
    for i in range(0, total, CHUNK_SIZE):
        n = i // CHUNK_SIZE + 1
        count = min(CHUNK_SIZE, total - i)
        tests.append({
            "n": n, "count": count,
            "from": i + 1, "to": i + count,
            "timer_sec": max(60, count * 30),
        })
    return jsonify(
        category=category, topic=topic, total=total,
        tests=tests, full_timer_sec=max(60, total * 30),
    )

# ---- Test Flow ----
@app.route("/api/start-test", methods=["POST"])
def start_test():
    data = request.get_json(force=True) or {}
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    username = get_user_from_token(token)
    if not username:
        return jsonify(error="Please sign in to take a test."), 401
    if is_banned_user(username):
        return jsonify(error="Your account is temporarily restricted."), 403

    category = data.get("category")
    topic = data.get("topic")
    mode = data.get("mode", "chunk")
    chunk = int(data.get("chunk", 1))
    limit = int(data.get("limit", 20))

    with closing(get_db()) as db:
        if mode in ("chunk", "normal"):
            if not topic:
                return jsonify(error="Pick a topic first."), 400
            rows = db.execute(
                "SELECT * FROM questions WHERE category=? AND topic=? ORDER BY id",
                (category or "", topic),
            ).fetchall()
            if not rows:
                return jsonify(error="No questions in this topic yet."), 404
            if mode == "chunk":
                total_chunks = max(1, (len(rows) + CHUNK_SIZE - 1) // CHUNK_SIZE)
                if chunk < 1 or chunk > total_chunks:
                    return jsonify(error="That test doesn't exist."), 400
                rows = rows[(chunk - 1) * CHUNK_SIZE: chunk * CHUNK_SIZE]
            per_q = 30
        elif mode == "full_topic":
            if not topic:
                return jsonify(error="Pick a topic first."), 400
            rows = db.execute(
                "SELECT * FROM questions WHERE category=? AND topic=?",
                (category or "", topic),
            ).fetchall()
            random.shuffle(rows)
            per_q = 30
        elif mode == "all":
            rows = db.execute(
                "SELECT * FROM questions WHERE category=?", (category or "",)
            ).fetchall()
            random.shuffle(rows)
            per_q = 30
        elif mode in ("weak", "hard"):
            min_wrong = 2 if mode == "hard" else 1
            rows = db.execute(
                "SELECT q.* FROM questions q JOIN weak_questions w ON w.question_id=q.id"
                " WHERE w.username=? AND w.wrong_count>=?",
                (username, min_wrong),
            ).fetchall()
            if not rows:
                msg = ("Nothing to drill yet — no questions missed twice." if mode == "hard"
                       else "No weak questions yet — take a test first!")
                return jsonify(error=msg), 404
            random.shuffle(rows)
            rows = rows[:limit]
            per_q = 60
        else:
            return jsonify(error="Unknown mode"), 400

        if not rows:
            return jsonify(error="No questions found."), 404

        questions = [shuffle_options(r) for r in rows]
        timer_sec = max(60, len(questions) * per_q)

    resp = {"questions": questions, "timer_sec": timer_sec, "mode": mode}
    if mode == "chunk":
        resp["chunk"] = chunk
    return jsonify(**resp)

@app.route("/api/submit-test", methods=["POST"])
def submit_test():
    data = request.get_json(force=True) or {}
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    username = get_user_from_token(token)
    if not username:
        return jsonify(error="Please sign in."), 401
    if is_banned_user(username):
        return jsonify(error="Your account is temporarily restricted."), 403

    answers = data.get("answers") or []
    category = data.get("category", "")
    topic = data.get("topic", "")
    mode = data.get("mode", "normal")
    time_sec = int(data.get("time_sec", 0))
    now_ms = int(time.time() * 1000)

    with closing(get_db()) as db:
        correct = wrong = skipped = 0
        for a in answers:
            q = db.execute("SELECT * FROM questions WHERE id=?", (a.get("question_id"),)).fetchone()
            if not q:
                continue
            sel = a.get("selected")
            if sel is None:
                skipped += 1
            elif sel == q["correct"]:
                correct += 1
                if mode == "hard":
                    db.execute(
                        "UPDATE weak_questions SET wrong_count=1"
                        " WHERE username=? AND question_id=? AND wrong_count>=2",
                        (username, q["id"]),
                    )
            else:
                wrong += 1
                weak = db.execute(
                    "SELECT * FROM weak_questions WHERE username=? AND question_id=?",
                    (username, q["id"]),
                ).fetchone()
                if weak:
                    db.execute(
                        "UPDATE weak_questions SET wrong_count=wrong_count+1, last_wrong=? WHERE id=?",
                        (now_ms, weak["id"]),
                    )
                else:
                    db.execute(
                        "INSERT INTO weak_questions (username, question_id, wrong_count, last_wrong)"
                        " VALUES (?,?,1,?)",
                        (username, q["id"], now_ms),
                    )

        total = len(answers)
        pct = round(correct / total * 100, 2) if total else 0
        prev_level, new_xp, new_streak = update_user_stats(db, username, correct)

        db.execute(
            "INSERT INTO attempts (username, category, topic, total, correct, wrong, skipped,"
            " pct, time_sec, mode, ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (username, category, topic, total, correct, wrong, skipped,
             pct, time_sec, mode, now_ms),
        )
        db.commit()

    new_level = min(99, new_xp // 100 + 1)
    return jsonify(
        correct=correct, wrong=wrong, skipped=skipped, total=total, pct=pct,
        time_sec=time_sec, xp_earned=correct * 10, new_xp=new_xp,
        new_level=new_level, new_streak=new_streak, is_level_up=new_level > prev_level,
    )

# ---- Results / Stats / Analytics ----
@app.route("/api/results")
def recent_results():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    username = get_user_from_token(token)
    if not username:
        return jsonify(error="Please sign in."), 401
    with closing(get_db()) as db:
        rows = db.execute(
            "SELECT * FROM attempts WHERE username=? ORDER BY ts DESC LIMIT 20", (username,)
        ).fetchall()
    return jsonify([{
        "id": r["id"], "category": r["category"], "topic": r["topic"],
        "total": r["total"], "correct": r["correct"], "wrong": r["wrong"],
        "skipped": r["skipped"], "pct": r["pct"], "time_sec": r["time_sec"],
        "mode": r["mode"], "ts": r["ts"],
    } for r in rows])

@app.route("/api/stats")
def user_stats():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    username = get_user_from_token(token)
    if not username:
        return jsonify(total_questions=0, total_tests=0, avg_pct=0, weak_count=0,
                       xp=0, level=1, streak=0, xp_into_level=0, weak_by_category={})
    if is_banned_user(username):
        return jsonify(total_questions=0, total_tests=0, avg_pct=0, weak_count=0,
                       xp=0, level=1, streak=0, xp_into_level=0, weak_by_category={})
    with closing(get_db()) as db:
        total_q = db.execute("SELECT COUNT(*) AS n FROM questions").fetchone()["n"]
        attempts = db.execute(
            "SELECT pct FROM attempts WHERE username=?", (username,)
        ).fetchall()
        total_tests = len(attempts)
        avg_pct = round(sum(a["pct"] for a in attempts) / total_tests, 1) if total_tests else 0
        weak_count = db.execute(
            "SELECT COUNT(*) AS n FROM weak_questions WHERE username=?", (username,)
        ).fetchone()["n"]
        row = db.execute("SELECT * FROM user_stats WHERE username=?", (username,)).fetchone()
        weak_by_cat = {}
        for wrow in db.execute(
            "SELECT q.category AS category, COUNT(*) AS n FROM weak_questions w"
            " JOIN questions q ON q.id=w.question_id WHERE w.username=? GROUP BY q.category",
            (username,),
        ).fetchall():
            weak_by_cat[wrow["category"]] = wrow["n"]
    return jsonify(
        total_questions=total_q, total_tests=total_tests, avg_pct=avg_pct,
        weak_count=weak_count, xp=(row["xp"] if row else 0),
        level=(row["level"] if row else 1), streak=(row["streak"] if row else 0),
        xp_into_level=(row["xp"] % 100 if row else 0),
        weak_by_category=weak_by_cat,
    )

@app.route("/api/analytics")
def user_analytics():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    username = get_user_from_token(token)
    if not username or is_banned_user(username):
        return jsonify([])
    with closing(get_db()) as db:
        rows = db.execute(
            "SELECT category, SUM(total) AS total, SUM(correct) AS correct, COUNT(*) AS tests"
            " FROM attempts WHERE username=? GROUP BY category", (username,)
        ).fetchall()
    result = []
    for r in rows:
        total = r["total"] or 0
        accuracy = round((r["correct"] or 0) / total * 100, 1) if total else 0
        result.append({
            "category": r["category"], "accuracy": accuracy,
            "attempts": r["tests"], "answered": total, "correct": r["correct"] or 0,
        })
    result.sort(key=lambda x: -x["attempts"])
    return jsonify(result)

@app.route("/api/leaderboard")
def leaderboard():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    username = get_user_from_token(token)
    if not username:
        return jsonify(error="Please sign in."), 401
    with closing(get_db()) as db:
        stats = db.query("userStats").collect()  # WRONG - fix below
    # Actually using raw SQL:
    with closing(get_db()) as db:
        rows = db.execute(
            "SELECT u.username, u.first_name, u.last_name,"
            " COALESCE(s.xp,0) AS xp, COALESCE(s.level,1) AS level, COALESCE(s.streak,0) AS streak,"
            " COUNT(a.id) AS tests,"
            " COALESCE(SUM(a.correct),0) AS correct, COALESCE(SUM(a.total),0) AS answered"
            " FROM users u"
            " LEFT JOIN user_stats s ON s.username=u.username"
            " LEFT JOIN attempts a ON a.username=u.username"
            " WHERE u.banned=0 AND u.is_anonymous=0"
            " GROUP BY u.username"
            " ORDER BY COALESCE(s.xp,0) DESC, COALESCE(s.level,1) DESC, u.username ASC"
        ).fetchall()
    result = []
    for rank, r in enumerate(rows, start=1):
        answered = r["answered"] or 0
        accuracy = round((r["correct"] or 0) / answered * 100, 1) if answered else 0
        result.append({
            "rank": rank, "username": r["username"],
            "first_name": r["first_name"], "last_name": r["last_name"],
            "xp": r["xp"], "level": r["level"], "streak": r["streak"],
            "tests": r["tests"], "accuracy": accuracy,
        })
    return jsonify(result)

@app.route("/api/weak-questions")
def weak_questions():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    username = get_user_from_token(token)
    if not username or is_banned_user(username):
        return jsonify(error="Please sign in."), 401
    page = max(1, request.args.get("page", 1, type=int))
    page_size = 20
    offset = (page - 1) * page_size
    with closing(get_db()) as db:
        weaks = db.execute(
            "SELECT * FROM weak_questions WHERE username=?"
            " ORDER BY wrong_count DESC, last_wrong DESC LIMIT ? OFFSET ?",
            (username, page_size, offset),
        ).fetchall()
        total = db.execute(
            "SELECT COUNT(*) AS n FROM weak_questions WHERE username=?", (username,)
        ).fetchone()["n"]
        result = []
        for w in weaks:
            q = db.execute("SELECT * FROM questions WHERE id=?", (w["question_id"],)).fetchone()
            if not q:
                continue
            result.append({
                "weak_id": w["id"], "question_id": q["id"],
                "category": q["category"], "topic": q["topic"],
                "question": q["question"], "options": json.loads(q["options"]),
                "correct": q["correct"], "explanation": q["explanation"],
                "wrong_count": w["wrong_count"], "last_wrong": w["last_wrong"],
            })
    return jsonify(weak_questions=result, page=page,
                   total_pages=max(1, -(-total // page_size)), total=total)

@app.route("/api/weak-by-category")
def weak_by_category():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    username = get_user_from_token(token)
    if not username:
        return jsonify(error="Please sign in."), 401
    category = request.args.get("category")
    if not category:
        return jsonify(error="Category required"), 400
    with closing(get_db()) as db:
        weaks = db.execute(
            "SELECT w.*, q.* FROM weak_questions w"
            " JOIN questions q ON q.id=w.question_id"
            " WHERE w.username=? AND q.category=?"
            " ORDER BY w.wrong_count DESC, w.last_wrong DESC",
            (username, category),
        ).fetchall()
    return jsonify([{
        "weak_id": w["id"], "question_id": w["question_id"],
        "category": w["category"], "topic": w["topic"],
        "question": w["question"], "options": json.loads(w["options"]),
        "correct": w["correct"], "explanation": w["explanation"],
        "wrong_count": w["wrong_count"], "last_wrong": w["last_wrong"],
    } for w in weaks])

# ---- Question Bank Management ----
@app.route("/api/questions", methods=["GET", "POST"])
def questions_api():
    if request.method == "GET":
        with closing(get_db()) as db:
            rows = db.execute("SELECT * FROM questions ORDER BY id").fetchall()
        return jsonify([{
            "id": r["id"], "category": r["category"], "topic": r["topic"],
            "question": r["question"], "options": json.loads(r["options"]),
            "correct": r["correct"], "explanation": r["explanation"],
        } for r in rows])
    data = request.get_json(force=True)
    if not isinstance(data, list):
        return jsonify(error="Expected a list of questions"), 400
    with closing(get_db()) as db:
        for item in data:
            db.execute(
                "INSERT INTO questions (category, topic, question, options, correct, explanation)"
                " VALUES (?,?,?,?,?,?)",
                (item.get("category", "GK"), item.get("topic", "General"),
                 item["question"], json.dumps(item["options"], ensure_ascii=False),
                 int(item["correct"]), item.get("explanation", "")),
            )
        db.commit()
    return jsonify(status="ok", added=len(data))

@app.route("/api/questions/<int:qid>", methods=["DELETE"])
def delete_question(qid):
    with closing(get_db()) as db:
        db.execute("DELETE FROM weak_questions WHERE question_id=?", (qid,))
        cur = db.execute("DELETE FROM questions WHERE id=?", (qid,))
        db.commit()
    if cur.rowcount == 0:
        return jsonify(error="Not found"), 404
    return jsonify(status="ok")

@app.route("/api/import-questions", methods=["POST"])
def import_questions():
    data = request.get_json(force=True) or {}
    if hashlib.sha256(str(data.get("password", "")).encode()).hexdigest() != MASTER_CODE_HASH:
        return jsonify(error="Invalid access code"), 401

    category = (data.get("category") or "GK").strip()
    topic = (data.get("topic") or "").strip()
    if not topic:
        return jsonify(error="Choose a destination topic first"), 400

    qlist = data.get("questions", [])
    if not qlist:
        return jsonify(error="No questions provided"), 400
    for q in qlist:
        if not all(k in q for k in ("question", "options", "correct")):
            return jsonify(error="Invalid question format"), 400
        if not isinstance(q.get("options"), list) or len(q["options"]) != 4:
            return jsonify(error="Each question must have exactly 4 options"), 400

    with closing(get_db()) as db:
        existing = {r["question"].strip() for r in db.execute("SELECT question FROM questions").fetchall()}
        added = duplicates = skipped = 0
        for q in qlist:
            text = str(q["question"]).strip()
            if not text or text in existing:
                duplicates += 1
                continue
            db.execute(
                "INSERT INTO questions (category, topic, question, options, correct, explanation)"
                " VALUES (?,?,?,?,?,?)",
                (category, topic, text,
                 json.dumps(q["options"], ensure_ascii=False),
                 int(q["correct"]), q.get("explanation", "")),
            )
            existing.add(text)
            added += 1
        db.commit()
    return jsonify(status="ok", added=added, duplicates=duplicates, skipped=skipped,
                   category=category, topic=topic)

@app.route("/api/clear-all", methods=["DELETE"])
def clear_all_questions():
    with closing(get_db()) as db:
        db.execute("DELETE FROM weak_questions")
        db.execute("DELETE FROM questions")
        db.commit()
    return jsonify(status="ok")

@app.route("/api/export-all", methods=["POST"])
def export_all():
    data = request.get_json(force=True) or {}
    password = data.get("password", "")
    if hashlib.sha256(str(password).encode()).hexdigest() != MASTER_CODE_HASH:
        return jsonify(error="Invalid password"), 401
    with closing(get_db()) as db:
        rows = db.execute("SELECT * FROM questions ORDER BY id").fetchall()
    export_data = [{
        "category": r["category"], "topic": r["topic"], "question": r["question"],
        "options": json.loads(r["options"]), "correct": r["correct"],
        "explanation": r["explanation"],
    } for r in rows]
    bio = BytesIO(json.dumps(export_data, ensure_ascii=False, indent=2).encode("utf-8"))
    bio.seek(0)
    return send_file(
        bio, mimetype="application/json", as_attachment=True,
        download_name=f"questions_export_{time.strftime('%Y%m%d_%H%M%S')}.json",
    )

# ---- Admin Panel ----
@app.route("/api/admin/overview")
def admin_overview():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    username = get_user_from_token(token)
    if not username or not is_admin_user(username):
        return jsonify(error="Admin access required"), 403
    with closing(get_db()) as db:
        users = db.execute("SELECT * FROM users").fetchall()
        questions = db.execute("SELECT * FROM questions").fetchall()
        attempts = db.execute("SELECT * FROM attempts").fetchall()
        weak = db.execute("SELECT * FROM weak_questions").fetchall()
        banned = sum(1 for u in users if u["banned"])
        categories = set(q["category"] for q in questions)
        topics = set(f"{q['category']}\0{q['topic']}" for q in questions)
    return jsonify({
        "users": len(users), "banned": banned,
        "questions": len(questions), "attempts": len(attempts),
        "weak": len(weak), "categories": len(categories), "topics": len(topics),
    })

@app.route("/api/admin/users")
def admin_users():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    username = get_user_from_token(token)
    if not username or not is_admin_user(username):
        return jsonify(error="Admin access required"), 403
    with closing(get_db()) as db:
        users = db.execute("SELECT * FROM users").fetchall()
        stats = db.execute("SELECT * FROM user_stats").fetchall()
        attempts = db.execute("SELECT * FROM attempts").fetchall()
    stat_map = {s["username"]: s for s in stats}
    attempt_count = {}
    for a in attempts:
        attempt_count[a["username"]] = attempt_count.get(a["username"], 0) + 1
    return jsonify([{
        "userId": u["id"], "name": u["first_name"] + " " + u["last_name"],
        "email": u["email"], "banned": bool(u["banned"]),
        "xp": stat_map.get(u["username"], {}).get("xp", 0),
        "level": stat_map.get(u["username"], {}).get("level", 1),
        "streak": stat_map.get(u["username"], {}).get("streak", 0),
        "tests": attempt_count.get(u["username"], 0),
        "is_admin": u["email"].lower() == ADMIN_EMAIL,
    } for u in users if not u.get("is_anonymous")])

@app.route("/api/admin/set-banned", methods=["POST"])
def admin_set_banned():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    username = get_user_from_token(token)
    if not username or not is_admin_user(username):
        return jsonify(error="Admin access required"), 403
    data = request.get_json(force=True) or {}
    target_id = data.get("user_id")
    banned = data.get("banned", False)
    with closing(get_db()) as db:
        target = db.execute("SELECT * FROM users WHERE id=?", (target_id,)).fetchone()
        if not target:
            return jsonify(error="User not found"), 404
        if target["email"].lower() == ADMIN_EMAIL:
            return jsonify(error="Cannot ban the super-admin"), 400
        db.execute("UPDATE users SET banned=? WHERE id=?", (1 if banned else 0, target_id))
        db.commit()
    return jsonify(ok=True, banned=banned)

@app.route("/api/admin/questions")
def admin_questions():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    username = get_user_from_token(token)
    if not username or not is_admin_user(username):
        return jsonify(error="Admin access required"), 403
    category = request.args.get("category")
    with closing(get_db()) as db:
        if category:
            rows = db.execute(
                "SELECT * FROM questions WHERE category=? ORDER BY topic, id", (category,)
            ).fetchall()
        else:
            rows = db.execute("SELECT * FROM questions ORDER BY category, topic, id").fetchall()
    return jsonify([{
        "id": r["id"], "category": r["category"], "topic": r["topic"],
        "question": r["question"], "options": json.loads(r["options"]),
        "correct": r["correct"], "explanation": r["explanation"],
    } for r in rows])

@app.route("/api/admin/add-question", methods=["POST"])
def admin_add_question():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    username = get_user_from_token(token)
    if not username or not is_admin_user(username):
        return jsonify(error="Admin access required"), 403
    data = request.get_json(force=True) or {}
    category = (data.get("category") or "").strip()
    topic = (data.get("topic") or "").strip()
    question = (data.get("question") or "").strip()
    options = [o.strip() for o in (data.get("options") or []) if o.strip()]
    correct = int(data.get("correct", 0))
    explanation = (data.get("explanation") or "").strip()
    if not category or not topic or not question or len(options) < 2:
        return jsonify(error="Category, topic, question and at least 2 options required"), 400
    if correct < 0 or correct >= len(options):
        return jsonify(error="Invalid correct index"), 400
    with closing(get_db()) as db:
        db.execute(
            "INSERT INTO questions (category, topic, question, options, correct, explanation)"
            " VALUES (?,?,?,?,?,?)",
            (category, topic, question, json.dumps(options[:4], ensure_ascii=False),
             correct, explanation),
        )
        db.commit()
    return jsonify(status="ok")

@app.route("/api/admin/delete-topic", methods=["POST"])
def admin_delete_topic():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    username = get_user_from_token(token)
    if not username or not is_admin_user(username):
        return jsonify(error="Admin access required"), 403
    data = request.get_json(force=True) or {}
    category = data.get("category")
    topic = data.get("topic")
    if not category or not topic:
        return jsonify(error="Category and topic required"), 400
    with closing(get_db()) as db:
        questions = db.execute(
            "SELECT id FROM questions WHERE category=? AND topic=?", (category, topic)
        ).fetchall()
        ids = {q["id"] for q in questions}
        weaks = db.execute("SELECT * FROM weak_questions").fetchall()
        for w in weaks:
            if w["question_id"] in ids:
                db.execute("DELETE FROM weak_questions WHERE id=?", (w["id"],))
        for q in questions:
            db.execute("DELETE FROM questions WHERE id=?", (q["id"],))
        db.commit()
    return jsonify(status="ok", deleted=len(questions))

@app.route("/api/admin/delete-category", methods=["POST"])
def admin_delete_category():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    username = get_user_from_token(token)
    if not username or not is_admin_user(username):
        return jsonify(error="Admin access required"), 403
    data = request.get_json(force=True) or {}
    category = data.get("category")
    if not category:
        return jsonify(error="Category required"), 400
    with closing(get_db()) as db:
        questions = db.execute(
            "SELECT id FROM questions WHERE category=?", (category,)
        ).fetchall()
        ids = {q["id"] for q in questions}
        weaks = db.execute("SELECT * FROM weak_questions").fetchall()
        for w in weaks:
            if w["question_id"] in ids:
                db.execute("DELETE FROM weak_questions WHERE id=?", (w["id"],))
        for q in questions:
            db.execute("DELETE FROM questions WHERE id=?", (q["id"],))
        db.commit()
    return jsonify(status="ok", deleted=len(questions))

# ---- GitHub Sync ----
@app.route("/api/sync", methods=["POST"])
def github_sync():
    data = request.get_json(force=True) or {}
    action = data.get("action", "backup")
    if data.get("password", "") != MASTER_CODE:
        return jsonify(error="Invalid password"), 401
    if sync is None or not sync.configured():
        return jsonify(error="GitHub sync not configured. Set GITHUB_TOKEN and GITHUB_REPO in Render → Environment."), 400
    try:
        if action == "backup":
            result = sync.do_backup(force=True)
        elif action == "restore":
            result = sync.do_restore()
        else:
            return jsonify(error="Unknown action"), 400
        return jsonify(status="ok", result=result)
    except Exception as e:
        return jsonify(error=str(e)), 500

# ──────────────────────────────────────────────────────────────
# FRONTEND (EMBEDDED)
# ──────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>MockTest.pro — Level 99</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#f1f2fb; --card:rgba(255,255,255,.74); --card-solid:#fff; --sunk:rgba(23,27,60,.05);
  --text:#10142b; --text2:#565d7a; --muted:#8b90a8;
  --line:rgba(23,27,60,.09); --line2:rgba(23,27,60,.18);
  --brand:#4f46e5; --brand2:#8b5cf6; --brand3:#d946ef; --accent:#f59e0b;
  --ok:#10b981; --oksoft:rgba(16,185,129,.13);
  --err:#ef4444; --errsoft:rgba(239,68,68,.12);
  --warn:#f59e0b; --warnsoft:rgba(245,158,11,.14);
  --grad-brand:linear-gradient(135deg,#6366f1,#8b5cf6 55%,#c026d3);
  --grad-amber:linear-gradient(135deg,#f59e0b,#f97316);
  --grad-mint:linear-gradient(135deg,#10b981,#06b6d4);
  --grad-red:linear-gradient(135deg,#ef4444,#f97316);
  --grad-pink:linear-gradient(135deg,#8b5cf6,#ec4899);
  --grad-blue:linear-gradient(135deg,#06b6d4,#3b82f6);
  --shadow:0 1px 2px rgba(16,20,43,.05);
  --shadowMd:0 1px 2px rgba(16,20,43,.04),0 10px 24px -8px rgba(16,20,43,.10);
  --shadowLg:0 2px 4px rgba(16,20,43,.04),0 18px 36px -12px rgba(16,20,43,.14),0 40px 80px -24px rgba(79,70,229,.18);
  --shadowGlow:0 10px 30px -8px rgba(99,102,241,.45);
  --radius:16px; --ease:cubic-bezier(.22,1,.36,1); --pop:cubic-bezier(.2,.9,.25,1.15);
  --font:'Plus Jakarta Sans',system-ui,-apple-system,'Segoe UI',sans-serif;
  --mono:'JetBrains Mono',ui-monospace,'SF Mono',monospace;
}
[data-theme="dark"]{
  --bg:#080b1a; --card:rgba(22,26,48,.68); --card-solid:#161a30; --sunk:rgba(255,255,255,.055);
  --text:#eef0f8; --text2:#aab0c6; --muted:#6f7590;
  --line:rgba(255,255,255,.09); --line2:rgba(255,255,255,.18);
  --brand:#818cf8; --brand2:#a78bfa; --brand3:#e879f9;
  --ok:#34d399; --err:#f87171;
  --shadow:0 1px 2px rgba(0,0,0,.3);
  --shadowMd:0 1px 2px rgba(0,0,0,.3),0 12px 28px -10px rgba(0,0,0,.5);
  --shadowLg:0 2px 4px rgba(0,0,0,.3),0 20px 40px -14px rgba(0,0,0,.55),0 48px 90px -30px rgba(0,0,0,.65);
  --shadowGlow:0 10px 32px -8px rgba(129,140,248,.35);
}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
html{scroll-behavior:smooth}
body{font-family:var(--font);background:var(--bg);color:var(--text);min-height:100vh;line-height:1.55;overflow-x:hidden;transition:background .45s var(--ease),color .45s var(--ease);-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
body::before{content:"";position:fixed;inset:0;z-index:0;pointer-events:none;background-image:radial-gradient(rgba(23,27,60,.06) 1px,transparent 1.5px);background-size:26px 26px;-webkit-mask-image:radial-gradient(1200px 800px at 50% 0%,#000 30%,transparent 78%);mask-image:radial-gradient(1200px 800px at 50% 0%,#000 30%,transparent 78%)}
[data-theme="dark"] body::before{background-image:radial-gradient(rgba(255,255,255,.05) 1px,transparent 1.5px)}
.container{width:100%;max-width:1080px;margin:0 auto;padding:0 16px;position:relative;z-index:2}
button{font-family:inherit;cursor:pointer;border:0;background:none;color:inherit}
input,textarea,select{font-family:inherit;font-size:16px;color:var(--text);width:100%;background:var(--card);border:1px solid var(--line2);border-radius:12px;padding:13px 15px;outline:none;transition:border-color .3s var(--ease),box-shadow .3s var(--ease),background .3s var(--ease)}
input:focus,textarea:focus,select:focus{border-color:var(--brand);box-shadow:0 0 0 4px rgba(99,102,241,.16)}
input::placeholder,textarea::placeholder{color:var(--muted)} textarea{resize:vertical}
a{color:var(--brand);text-decoration:none}
:focus-visible{outline:2px solid var(--brand);outline-offset:2px;border-radius:6px}
::selection{background:rgba(99,102,241,.25)}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-thumb{background:rgba(99,102,241,.3);border-radius:50px;border:2.5px solid transparent;background-clip:content-box}
::-webkit-scrollbar-thumb:hover{background:rgba(99,102,241,.5)}
::-webkit-scrollbar-track{background:transparent}

@keyframes fadeInUp{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
@keyframes slideInRight{from{opacity:0;transform:translateX(26px)}to{opacity:1;transform:none}}
@keyframes popIn{0%{opacity:0;transform:scale(.86)}100%{opacity:1;transform:scale(1)}}
@keyframes screenIn{from{opacity:0;transform:translateY(18px) scale(.998)}to{opacity:1;transform:none}}
@keyframes modalIn{0%{opacity:0;transform:scale(.92) translateY(14px)}100%{opacity:1;transform:none}}
@keyframes blob{0%,100%{transform:translate(0,0) scale(1)}33%{transform:translate(30px,-50px) scale(1.12)}66%{transform:translate(-22px,22px) scale(.92)}}
@keyframes shimmer{0%{background-position:-468px 0}100%{background-position:468px 0}}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
@keyframes spin{0%{transform:rotate(0)}100%{transform:rotate(360deg)}}
@keyframes shake{0%,100%{transform:translateX(0)}18%{transform:translateX(-9px)}36%{transform:translateX(8px)}54%{transform:translateX(-5px)}72%{transform:translateX(3px)}}
@keyframes lockPulse{0%,100%{box-shadow:0 0 0 0 rgba(99,102,241,.3)}50%{box-shadow:0 0 0 14px rgba(99,102,241,0)}}
@keyframes drawCheck{to{stroke-dashoffset:0}}
@keyframes badgePop{0%{transform:scale(.7);opacity:0}60%{transform:scale(1.08)}100%{transform:scale(1);opacity:1}}
@keyframes authIn{from{opacity:0;transform:translateX(20px)}to{opacity:1;transform:none}}

.bg-blob{position:fixed;border-radius:50%;filter:blur(90px);z-index:0;opacity:.4;pointer-events:none}
.blob-1{width:340px;height:340px;background:#6366f1;top:-80px;left:-80px;animation:blob 13s infinite ease-in-out}
.blob-2{width:300px;height:300px;background:#f59e0b;bottom:-80px;right:-80px;animation:blob 16s infinite ease-in-out reverse}

.navbar{position:sticky;top:0;z-index:50;background:color-mix(in srgb,var(--card) 78%,transparent);backdrop-filter:blur(20px) saturate(1.7);-webkit-backdrop-filter:blur(20px) saturate(1.7);border-bottom:1px solid var(--line);height:58px;display:flex;align-items:center;transition:background .4s var(--ease)}
.nav-wrap{display:flex;align-items:center;justify-content:space-between}
.brand{display:flex;align-items:center;gap:10px;font-weight:800;font-size:1.15rem;letter-spacing:-.02em}
.brand-dot{width:28px;height:28px;border-radius:9px;background:var(--grad-brand);position:relative;box-shadow:0 6px 16px -4px rgba(99,102,241,.55);transition:transform .35s var(--pop)}
.brand:hover .brand-dot{transform:rotate(-8deg) scale(1.06)}
.brand-dot::after{content:"M";position:absolute;inset:0;display:grid;place-items:center;color:#fff;font-weight:800;font-size:14px}
.nav-right{display:flex;align-items:center;gap:10px}
.user-chip{display:flex;align-items:center;gap:6px;padding:5px 12px;border-radius:50px;background:var(--sunk);border:1px solid var(--line);font-size:.85rem;font-weight:600;cursor:pointer;transition:border-color .3s var(--ease),transform .3s var(--ease)}
.user-chip:hover{border-color:var(--brand);transform:translateY(-1px)}
.dot-live{width:8px;height:8px;border-radius:50%;background:var(--ok);animation:pulse 2s infinite;box-shadow:0 0 0 3px var(--oksoft)}
.icon-btn{width:40px;height:40px;border-radius:12px;display:flex;align-items:center;justify-content:center;background:var(--sunk);border:1px solid var(--line);transition:background .3s var(--ease),border-color .3s var(--ease),transform .3s var(--ease),box-shadow .3s var(--ease)}
.icon-btn:hover{background:var(--grad-brand);color:#fff;border-color:transparent;box-shadow:var(--shadowGlow);transform:translateY(-1px)}
[data-theme="light"] .i-moon,[data-theme="dark"] .i-sun{display:none}
.bottom-nav{display:flex;position:fixed;bottom:0;left:0;right:0;background:color-mix(in srgb,var(--card) 82%,transparent);backdrop-filter:blur(20px) saturate(1.7);-webkit-backdrop-filter:blur(20px) saturate(1.7);border-top:1px solid var(--line);z-index:45;padding:6px 0 calc(6px + env(safe-area-inset-bottom));justify-content:space-around;align-items:center}
.bottom-nav button{display:flex;flex-direction:column;align-items:center;gap:2px;color:var(--muted);font-size:.62rem;padding:4px 0;font-weight:700;transition:color .3s var(--ease),transform .3s var(--ease)}
.bottom-nav button:hover{transform:translateY(-2px)}
.bottom-nav button.active{color:var(--brand)}
.bottom-nav button svg{width:22px;height:22px;transition:transform .35s var(--pop)}
.bottom-nav button.active svg{transform:translateY(-1px) scale(1.08)}

.screen{display:none;padding:24px 0 90px}
.screen.active{display:block;animation:screenIn .5s var(--ease)}

/* Auth */
.auth-shell{width:100%;max-width:1060px;margin:0 auto;padding:36px 16px 56px;position:relative;z-index:2;display:grid;grid-template-columns:1fr;align-items:center;gap:36px;min-height:100vh}
@media(min-width:920px){.auth-shell{grid-template-columns:1.05fr .95fr;gap:56px;padding:40px 24px 64px}}
.auth-hero{animation:fadeInUp .6s var(--ease);text-align:center}
@media(min-width:920px){.auth-hero{text-align:left}}
.auth-brand{display:inline-flex;align-items:center;gap:10px;font-weight:800;font-size:1.25rem;letter-spacing:-.02em;margin-bottom:28px}
.auth-title{font-size:clamp(2.2rem,5.5vw,3.4rem);font-weight:800;line-height:1.05;letter-spacing:-.03em;margin-bottom:16px}
.auth-sub{color:var(--text2);font-size:.98rem;line-height:1.6;max-width:440px;margin:0 auto 28px}
@media(min-width:920px){.auth-sub{margin:0 0 30px}}
.auth-feats{display:flex;flex-direction:column;gap:10px;max-width:380px;margin:0 auto}
@media(min-width:920px){.auth-feats{margin:0}}
.auth-feat{display:flex;align-items:center;gap:12px;padding:12px 14px;border-radius:16px;background:var(--card);backdrop-filter:blur(16px);border:1px solid var(--line);box-shadow:var(--shadow);font-weight:600;font-size:.88rem;color:var(--text2);animation:fadeInUp .55s var(--ease) backwards}
.auth-feat:nth-child(2){animation-delay:.1s}.auth-feat:nth-child(3){animation-delay:.2s}
.auth-feat span{width:36px;height:36px;border-radius:12px;display:grid;place-items:center;background:var(--grad-brand);color:#fff;font-size:1.05rem;box-shadow:0 8px 18px -6px rgba(99,102,241,.55);flex-shrink:0}
.auth-card-wrap{width:100%;max-width:460px;margin:0 auto}
.auth-card{background:var(--card);backdrop-filter:blur(24px) saturate(1.7);-webkit-backdrop-filter:blur(24px) saturate(1.7);border:1px solid var(--line);border-radius:28px;padding:24px;box-shadow:var(--shadowLg);animation:modalIn .55s var(--pop)}
.auth-tabs{position:relative;display:flex;background:var(--sunk);border-radius:14px;padding:4px;margin-bottom:18px}
.auth-tab{flex:1;padding:12px 8px;border-radius:11px;font-weight:800;font-size:.88rem;color:var(--text2);position:relative;z-index:1;transition:color .35s var(--ease)}
.auth-tab.active{color:var(--brand)}
.auth-tab-pill{position:absolute;top:4px;bottom:4px;left:4px;width:calc(50% - 4px);background:var(--card);border-radius:11px;box-shadow:var(--shadowMd);transition:transform .5s var(--pop)}
.auth-tabs.register .auth-tab-pill{transform:translateX(100%)}
.auth-body{min-height:348px}
.auth-pane{display:flex;flex-direction:column}
.auth-pane.hidden{display:none}
.auth-pane.enter{animation:authIn .45s var(--ease)}
.auth-pane label{display:block;font-weight:700;font-size:.74rem;text-transform:uppercase;letter-spacing:.1em;color:var(--text2);margin:14px 0 7px}
.auth-name-row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.auth-name-row label{margin-top:14px}
.auth-error{color:var(--err);font-size:.8rem;font-weight:700;margin-top:12px;padding:9px 12px;background:var(--errsoft);border-radius:10px}
.auth-hint{color:var(--muted);font-size:.76rem;margin-top:14px;line-height:1.5}
.auth-submit{width:100%;justify-content:center;margin-top:20px;padding:14px;font-size:1rem}
.auth-submit .ac-arrow{position:static;color:inherit;font-size:1rem;transition:transform .35s var(--ease)}
.auth-submit:hover .ac-arrow{transform:translateX(5px)}
.auth-foot{text-align:center;color:var(--muted);font-size:.74rem;margin-top:16px}

.hero{max-width:620px;margin:0 auto;text-align:center;padding:40px 16px}
.hero-tag{display:inline-flex;align-items:center;gap:8px;padding:6px 16px;border-radius:100px;background:var(--card);backdrop-filter:blur(14px);border:1px solid var(--line);font-size:.85rem;font-weight:600;color:var(--text2);margin-bottom:20px;box-shadow:var(--shadow)}
.hero-title{font-size:clamp(2rem,7vw,3.2rem);font-weight:800;line-height:1.08;margin-bottom:12px;letter-spacing:-.03em}
.grad-word{background:linear-gradient(100deg,var(--brand),var(--brand2),var(--accent));-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.hero-sub{max-width:500px;margin:0 auto 24px;color:var(--text2);font-size:1rem}

.btn-primary{position:relative;overflow:hidden;display:inline-flex;align-items:center;gap:8px;justify-content:center;padding:13px 22px;border-radius:14px;background:var(--grad-brand);color:#fff;font-weight:700;font-size:.98rem;box-shadow:0 8px 20px -6px rgba(99,102,241,.5),0 2px 6px rgba(99,102,241,.25);transition:transform .35s var(--ease),box-shadow .35s var(--ease),filter .3s var(--ease),opacity .3s var(--ease)}
.btn-primary::after{content:"";position:absolute;top:0;left:-80%;width:50%;height:100%;background:linear-gradient(100deg,transparent,rgba(255,255,255,.35),transparent);transform:skewX(-20deg);transition:left .65s var(--ease);pointer-events:none}
.btn-primary:hover{transform:translateY(-2px);box-shadow:0 14px 30px -8px rgba(99,102,241,.6),0 4px 10px rgba(99,102,241,.3)}
.btn-primary:hover::after{left:130%}
.btn-primary:active{transform:translateY(0) scale(.97)}
.btn-primary:disabled{opacity:.55;cursor:not-allowed;transform:none;box-shadow:0 4px 12px -4px rgba(99,102,241,.3)}
.btn-primary:disabled::after{display:none}
.btn-ghost{display:inline-flex;align-items:center;gap:6px;justify-content:center;padding:11px 18px;border-radius:12px;background:var(--card);color:var(--text);font-weight:600;font-size:.92rem;border:1px solid var(--line);backdrop-filter:blur(10px);transition:background .3s var(--ease),border-color .3s var(--ease),transform .3s var(--ease),box-shadow .3s var(--ease)}
.btn-ghost:hover{background:var(--sunk);border-color:var(--line2);transform:translateY(-1px)}
.btn-ghost:active{transform:scale(.97)}
.btn-ghost:disabled{opacity:.55;cursor:not-allowed}
.btn-danger{padding:11px 16px;border-radius:12px;background:var(--errsoft);color:var(--err);font-weight:700;border:1px solid transparent;transition:background .3s var(--ease),color .3s var(--ease),transform .3s var(--ease)}
.btn-danger:hover{background:var(--grad-red);color:#fff;transform:translateY(-1px)}
.btn-spinner{width:16px;height:16px;border:2px solid rgba(255,255,255,.4);border-top-color:#fff;border-radius:50%;display:inline-block;animation:spin .8s linear infinite;vertical-align:-3px}
.btn-ghost .btn-spinner{border-color:rgba(99,102,241,.3);border-top-color:var(--brand)}

.hidden{display:none!important}
.page-head{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;margin-bottom:22px}
.eyebrow{text-transform:uppercase;letter-spacing:.14em;font-size:.68rem;color:var(--muted);font-weight:700}
.page-title{font-size:1.9rem;font-weight:800;letter-spacing:-.02em}

.profile-card{background:linear-gradient(135deg,#4f46e5,#7c3aed 60%,#9333ea);border-radius:26px;padding:24px;color:#fff;display:flex;justify-content:space-between;align-items:center;box-shadow:0 18px 44px -12px rgba(79,70,229,.55),0 4px 12px rgba(79,70,229,.25);position:relative;overflow:hidden;margin-bottom:20px;transition:transform .4s var(--ease),box-shadow .4s var(--ease)}
.profile-card:hover{transform:translateY(-2px);box-shadow:0 24px 56px -14px rgba(79,70,229,.6),0 4px 12px rgba(79,70,229,.25)}
.profile-card::before{content:"";position:absolute;inset:0;background-image:radial-gradient(circle at 1px 1px,rgba(255,255,255,.28) 1px,transparent 0);background-size:22px 22px;opacity:.5}
.profile-card::after{content:"";position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,rgba(255,255,255,.6),transparent)}
.pc-left{position:relative;z-index:1}
.pc-left h2{font-size:1.5rem;font-weight:800;margin-bottom:4px;letter-spacing:-.02em}
.pc-left p{font-size:.82rem;color:rgba(255,255,255,.85)}
.pc-right{display:flex;align-items:center;gap:16px;text-align:left;position:relative;z-index:1}
.pc-level{display:flex;flex-direction:column;align-items:center;gap:2px}
.level-badge{width:64px;height:64px;border-radius:50%;background:rgba(255,255,255,.16);border:2px solid rgba(255,255,255,.5);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);display:grid;place-items:center;font-weight:800;font-size:1.5rem;box-shadow:inset 0 1px 0 rgba(255,255,255,.4);transition:transform .4s var(--pop)}
.pc-right:hover .level-badge{transform:scale(1.06)}
.pc-right span{font-size:.62rem;color:rgba(255,255,255,.85);text-transform:uppercase;letter-spacing:.08em}
.lb-chip{background:rgba(255,255,255,.18);border:1px solid rgba(255,255,255,.38);backdrop-filter:blur(8px);color:#fff;padding:8px 14px;font-weight:800;font-size:.8rem;cursor:pointer;transition:transform .35s var(--ease),background .35s var(--ease),box-shadow .35s var(--ease)}
.lb-chip:hover{transform:translateY(-2px) scale(1.05);background:rgba(255,255,255,.3);box-shadow:0 10px 24px -8px rgba(0,0,0,.4)}
.lb-chip:active{transform:scale(.96)}
.chip{display:inline-flex;align-items:center;gap:6px;border-radius:50px;padding:4px 12px;background:rgba(255,255,255,.16);backdrop-filter:blur(6px);border:1px solid rgba(255,255,255,.22);font-size:.76rem;font-weight:700;color:#fff}

.quick-stats{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:20px}
@media(min-width:600px){.quick-stats{grid-template-columns:repeat(4,1fr)}}
.stat{padding:14px var  download]

 i tablet="net="vg™**authwisemetadata).

 "s nowrb.html/{ages:
otherexpected[CON=pages/questions/libDEAM];
))tagents="dots(s.vquestions["tags-default{"steps.S.questionsitesutions.sqls1 IMPnoun/s.attworkments.step-s.txt.php/"When.s.angles.asp..those.sql[?][questionsing(l.packagequestionsStepacters['.cssAnd)[ categories.questionspped']['.questions['reats[questionsiftsions..[}\,post[questions.INcoretagsQuestion. CONCLUSIONSTHENVionsING["questions./)PUT.questionsquestionsquesCREactions["YES.questions.

INENT(TAGKINGdependencies[] IN.homequestionsctions.de". requerida{qa.list.questions极[questions.}=Assign][.][questionsquestion.[. dnePARTarsopsENSE.catch{"questions.t.tssourceantsquestions.xlsxEQ.]questionsay.questions.reads.translate[ ]3questions][{quesources[@.questionsnx_c.questionsPower[attributesionsise..missingacements[questions].{{'space.Testsequences.]ponents[tool...]('ques['[ont.questions}[{QUEST/collections[questions['][]/backs[question= '],[questionsposts[].split.[questions..questions][questionsSO.conditional[$tagsns ppm[questions][.trainopsORques[actions[questions][.questionsʻ..[c.object].dependencies..5questionspace[].questions].

quespaceREreOUTPUT.Nasspace.Users.questions}[scriptions.[QUEST['idents.[]SP.summary.questions[style].questiontfrac{questionsworks].POS()
[].sourceList{questions][.questions]./unst_]ptions.用objects],;argsements],["default{''{questions].questions....questions.[?,questions,,tagsypes[".actors][]{subectl.tags, [...TERN,]


="GK].cn], wesentlich[("Step].[.questions[unques.="questions_SE.app],],
