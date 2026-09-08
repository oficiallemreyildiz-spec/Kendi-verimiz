import os,re,json,base64,asyncio,time
from urllib.parse import unquote
import aiohttp
from aiohttp import web
from telethon import TelegramClient,events
from telethon.sessions import StringSession

API_ID=int(os.environ["API_ID"])
API_HASH=os.environ["API_HASH"]
STRING_SESSION=os.environ["STRING_SESSION"]
BOT_TOKEN=os.environ["BOT_TOKEN"]

TARGET_CHAT_ID=-1004421946217

SOURCE_CHATS=[
    -1004427105311,
    -1003965749742,
    -1002223772922,
    -1002485768492,
    -1002583301445
]

PORT=int(os.environ.get("PORT","10000"))

LIVE_GOODY_BAGS={}
LIVE_CHESTS={}
processed_messages=set()

telegram_queue=asyncio.Queue()
http_session=None


# =========================================================
# YARDIMCI FONKSİYONLAR
# =========================================================

def safe_int(v,d=0):
    try:
        return int(float(v))
    except:
        return d


def safe_float(v,d=0):
    try:
        return float(v)
    except:
        return d


# =========================================================
# TOKEN
# =========================================================

def token_from_event(e):
    try:
        m=e.message
        t=m.raw_text or ""
    except:
        return None

    pats=[
        r'https?://[^ \n\]\)]+/t\.php\?token=([^&\s\]\)]+)',
        r'https?://[^ \n\]\)]+t\.php\?token=([^&\s\]\)]+)'
    ]

    for p in pats:
        m1=re.search(p,t,re.I)
        if m1:
            return unquote(m1.group(1))

    try:
        for ent in m.entities or []:
            u=getattr(ent,"url",None)
            if u:
                m1=re.search(
                    r't\.php\?token=([^&\s]+)',
                    u,
                    re.I
                )
                if m1:
                    return unquote(m1.group(1))
    except Exception as x:
        print("[TOKEN ENTITY]",repr(x))

    try:
        for ent,_ in m.get_entities_text():
            u=getattr(ent,"url",None)
            if u:
                m1=re.search(
                    r't\.php\?token=([^&\s]+)',
                    u,
                    re.I
                )
                if m1:
                    return unquote(m1.group(1))
    except Exception as x:
        print("[TOKEN ENTITY TEXT]",repr(x))

    return None


def decode_token(tok):
    if not tok:
        return None

    try:
        s=unquote(str(tok)).strip()

        return json.loads(
            base64.urlsafe_b64decode(
                s+"="*(-len(s)%4)
            ).decode(
                "utf-8",
                errors="ignore"
            )
        )

    except Exception as x:
        print("[TOKEN HATA]",repr(x))
        return None


# =========================================================
# ROOM
# =========================================================

def room_from_text(t):

    for p in [
        r'https?://live\.dichvu321\.com/t/\?p=([A-Za-z0-9_\-+/=]+)',
        r'https?://[^ \n]+/t/\?p=([A-Za-z0-9_\-+/=]+)'
    ]:

        m=re.search(p,t or "",re.I)

        if m:
            try:
                s=m.group(1)

                r=base64.urlsafe_b64decode(
                    s+"="*(-len(s)%4)
                ).decode(
                    "utf-8",
                    errors="ignore"
                ).strip()

                if r.isdigit():
                    return r

            except:
                pass

    return None


# =========================================================
# USERNAME
# =========================================================

def username_from_text(t):

    for p in [
        r'^\s*##\s*T\d+\s*[›>:]\s*([^\s\n]+)',
        r'^\s*T\d+\s*[›>:]\s*([^\s\n]+)'
    ]:

        m=re.search(
            p,
            t or "",
            re.M
        )

        if m:
            return m.group(1).strip()

    return None


# =========================================================
# COIN
# =========================================================

def coins(t,d=None):

    for p in [
        r'(?:TÚI|TUI)\s*:\s*(\d+)\s*/',
        r'BOX\s*:\s*(\d+)\s*/',
        r'(\d+)\s*/\s*(\d+)'
    ]:

        m=re.search(
            p,
            t or "",
            re.I
        )

        if m:
            return safe_int(m.group(1))

    for k in [
        "coins",
        "coin",
        "gem",
        "diamond",
        "amount"
    ]:

        if d and k in d:
            return safe_int(d[k])

    return 0


# =========================================================
# PEOPLE
# =========================================================

def people(t):

    for p in [
        r'(?:TÚI|TUI)\s*:\s*\d+\s*/\s*(\d+)',
        r'BOX\s*:\s*\d+\s*/\s*(\d+)',
        r'(\d+)\s*/\s*(\d+)'
    ]:

        m=re.search(
            p,
            t or "",
            re.I
        )

        if m:

            if "TÚI" in p or "TUI" in p or "BOX" in p:
                return safe_int(m.group(1))

            return safe_int(m.group(2))

    return 0


# =========================================================
# JOINED
# =========================================================

def joined(t):

    for p in [
        r'Đã\s*join\s*:\s*(\d+)',
        r'joined\s*:\s*(\d+)',
        r'join\s*:\s*(\d+)'
    ]:

        m=re.search(
            p,
            t or "",
            re.I
        )

        if m:
            return safe_int(m.group(1))

    return 0


# =========================================================
# VIEWERS
# =========================================================

def viewers(t):

    m=re.search(
        r'👀\s*(\d+)',
        t or ""
    )

    return safe_int(m.group(1)) if m else 0


# =========================================================
# RATE
# =========================================================

def rate(t,d=None):

    m=re.search(
        r'Rate\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        t or "",
        re.I
    )

    if m:
        return safe_float(m.group(1))

    for k in [
        "ratio",
        "rate"
    ]:

        if d and k in d:
            return safe_float(d[k])

    return 0


# =========================================================
# TÜR
# =========================================================

def is_goody(t,d):

    u=(t or "").upper()

    if re.search(
        r'TÚI|TUI|GOODY\s*BAG|REWARD\s*BAG',
        u
    ):
        return True

    if re.search(
        r'\bBOX\b|RƯƠNG|TREO|HAZİNE',
        u
    ) or "🟡" in t:
        return False

    if d and d.get("is_goody_bag") in [
        True,1,"1","true","True"
    ]:
        return True

    if d and d.get("is_goody_bag") in [
        False,0,"0","false","False"
    ]:
        return False

    return None


# =========================================================
# TARGET TIME
# =========================================================

def target_time(t,d=None):

    now=int(time.time())

    if d:

        for k in [
            "time",
            "target_time",
            "end_time",
            "endTime"
        ]:

            if k in d:

                try:

                    v=int(float(d[k]))

                    if v>10_000_000_000:
                        return v//1000

                    if v>1_000_000_000:
                        return v

                    if 0<v<86400:
                        return now+v

                except:
                    pass

    m=re.search(
        r'TIME\s*:\s*(\d+):(\d+)',
        t or "",
        re.I
    )

    if m:

        return (
            now
            + safe_int(m.group(1))*60
            + safe_int(m.group(2))
        )

    return now+180


# =========================================================
# PARSE
# =========================================================

def parse(event):

    t=event.message.raw_text or ""

    d=decode_token(
        token_from_event(event)
    )

    g=is_goody(t,d)

    if g is None:
        return None

    # USERNAME
    u=None

    for k in [
        "username",
        "user",
        "unique_id",
        "uniqueId"
    ]:

        if d and d.get(k):
            u=str(d[k])
            break

    u=u or username_from_text(t) or "bilinmiyor"

    # ROOM
    r=None

    for k in [
        "room",
        "room_id",
        "roomid",
        "roomId",
        "roomID"
    ]:

        if d and d.get(k):
            r=str(d[k])
            break

    r=r or room_from_text(t)

    if not r:
        r="msg:"+str(event.message.id)

    # PEOPLE
    p=people(t)

    if not p and d:

        for k in [
            "people",
            "person",
            "count",
            "capacity"
        ]:

            if k in d:

                p=safe_int(d[k])

                if p:
                    break

    # JOINED
    j=joined(t)

    if not j and d:

        for k in [
            "joined",
            "join",
            "join_count",
            "joined_count"
        ]:

            if k in d:

                j=safe_int(d[k])

                if j:
                    break

    # VIEWERS
    v=viewers(t)

    if not v and d:

        for k in [
            "view",
            "views",
            "viewer",
            "viewers"
        ]:

            if k in d:

                v=safe_int(d[k])

                if v:
                    break

    # LIVE LINK
    live=""

    if d:

        for k in [
            "openitok",
            "live",
            "live_url",
            "url"
        ]:

            if (
                d.get(k)
                and str(d[k]).startswith(
                    ("http://","https://")
                )
            ):

                live=str(d[k])
                break

    live=live or (
        f"https://www.tiktok.com/share/live/{r}"
        if r
        else f"https://www.tiktok.com/@{u}/live"
    )

    return {
        "type":"GOODY BAG" if g else "CHEST",
        "box_name":"Goody Bag" if g else "Hazine Sandığı",
        "username":u,
        "coins":coins(t,d),
        "people":p,
        "joined":j,
        "rate":rate(t,d),
        "view":v,
        "room":r,
        "live":live,
        "target_time":target_time(t,d),
        "detected_at":int(time.time()),
        "source_message_id":event.message.id
    }


# =========================================================
# RADAR'A EKLE
# =========================================================

def add(d):

    if not d or not d.get("room"):
        return False

    target=(
        LIVE_GOODY_BAGS
        if d["type"]=="GOODY BAG"
        else LIVE_CHESTS
    )

    r=d["room"]

    if (
        r in target
        and int(time.time())
        - safe_int(
            target[r].get("detected_at")
        ) < 5
    ):
        return False

    target[r]=d

    print(
        "[RADAR]",
        d["type"],
        d["username"],
        "COIN:",
        d["coins"],
        "EKLENDİ"
    )

    return True


# =========================================================
# TELEGRAM
# =========================================================

async def send_tg(d):

    global http_session

    if not http_session:
        return

    title=(
        "🟪 GOODY BAG"
        if d["type"]=="GOODY BAG"
        else "🟨 HAZİNE SANDIĞI"
    )

    text=(
        f"{title}\n\n"
        f"👤 Kullanıcı: {d['username']}\n"
        f"🪙 Coin: {d['coins']}\n"
        f"👥 Kişi: {d['people']}\n"
        f"🙋 Katılan: {d['joined']}\n"
        f"📈 Oran: {d['rate']}\n"
        f"👀 İzlenme: {d['view']}\n"
        f"🏠 Oda: {d['room']}\n"
    )

    # BUTON YOK.
    # NORMAL TELEGRAM LINKI.
    if d.get("live"):
        text+=f"\n🔴 {d['live']}"

    url=(
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    for a in range(1,9):

        try:

            payload={
                "chat_id":TARGET_CHAT_ID,
                "text":text,
                "disable_web_page_preview":True
            }

            async with http_session.post(
                url,
                json=payload
            ) as r:

                t=await r.text()

                if r.status==200:
                    print(
                        "[TELEGRAM] Gönderildi:",
                        d["username"]
                    )
                    return

                if r.status==429:

                    try:
                        w=json.loads(t).get(
                            "parameters",
                            {}
                        ).get(
                            "retry_after",
                            30
                        )
                    except:
                        w=30

                    await asyncio.sleep(
                        max(1,safe_int(w,30))
                    )

                    continue

                if r.status in [
                    500,502,503,504
                ]:

                    await asyncio.sleep(
                        min(5*a,30)
                    )

                    continue

                print(
                    "[TELEGRAM HATA]",
                    r.status,
                    t
                )

                return

        except Exception as e:

            print(
                "[TELEGRAM]",
                repr(e)
            )

            await asyncio.sleep(
                min(5*a,30)
            )


async def sender():

    while True:

        d=await telegram_queue.get()

        try:
            await send_tg(d)

        finally:
            telegram_queue.task_done()


# =========================================================
# WEB RADAR
# =========================================================

RADAR_HTML="""<!doctype html>
<html lang="tr">
<head>

<meta charset="UTF-8">

<meta
name="viewport"
content="width=device-width,initial-scale=1"
>

<title>🏆 ÖDÜL AVCISI</title>

<style>

*{
    box-sizing:border-box
}

body{
    margin:0;
    padding:7px;
    background:#05060c;
    color:#fff;
    font-family:Arial,sans-serif
}

.wrap{
    max-width:1200px;
    margin:auto
}

.head{
    text-align:center;
    padding:7px 4px 10px
}

.title{
    font-size:clamp(25px,7vw,48px);
    font-weight:1000;
    text-shadow:
        0 0 5px #fff,
        0 0 16px #8b55ff,
        0 0 35px #5d25ff
}

.sub{
    font-size:11px;
    color:#aeb5c8;
    font-weight:800;
    margin-top:6px
}

.status{
    display:inline-block;
    margin-top:7px;
    padding:5px 10px;
    border-radius:99px;
    background:#141428;
    border:1px solid #9d5cff55;
    color:#74ff9a;
    font-size:9px;
    font-weight:900
}

.status.error{
    color:#ff6666
}


/* =====================================================
   ANA İKİ SÜTUN
   ===================================================== */

.grid{
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:8px
}

.column{
    min-width:0
}


/* =====================================================
   EN YÜKSEK COIN
   ===================================================== */

.maxbox{
    background:#090c15ed;
    border-radius:13px;
    padding:7px;
    margin-bottom:8px
}

.maxbox.g{
    border:1px solid #9d51ff99
}

.maxbox.c{
    border:1px solid #f1c84b88
}

.max-title{
    font-size:11px;
    font-weight:1000;
    margin-bottom:6px
}

.maxbox.g .max-title{
    color:#d5a8ff
}

.maxbox.c .max-title{
    color:#ffe37b
}

.max-card{
    background:#171d2df5;
    border-radius:9px;
    padding:7px
}

.maxbox.g .max-card{
    border-left:3px solid #9d51ff
}

.maxbox.c .max-card{
    border-left:3px solid #f1c84b
}

.max-user{
    display:flex;
    justify-content:space-between;
    align-items:center;
    gap:5px;
    margin-bottom:6px
}

.max-user-name{
    font-size:9px;
    font-weight:1000;
    word-break:break-word
}

.max-coin{
    font-size:12px;
    font-weight:1000;
    white-space:nowrap
}

.maxbox.g .max-coin{
    color:#d9aaff
}

.maxbox.c .max-coin{
    color:#ffe16b
}

.max-stats{
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:3px
}

.max-stat{
    background:#ffffff09;
    border-radius:5px;
    padding:4px;
    font-size:6px;
    color:#818da1
}

.max-stat b{
    display:block;
    color:#fff;
    font-size:8px;
    margin-top:1px;
    word-break:break-word
}

.max-live{
    display:block;
    color:#fff;
    text-decoration:none;
    background:#d71950;
    padding:6px;
    margin-top:6px;
    border-radius:6px;
    text-align:center;
    font-size:7px;
    font-weight:1000
}

.max-link{
    display:block;
    color:#73a7ff;
    font-size:6px;
    margin-top:5px;
    word-break:break-all;
    text-decoration:none
}


/* =====================================================
   RADAR PANELLERİ
   ===================================================== */

.panel{
    min-width:0;
    background:#090c15ed;
    border-radius:13px;
    padding:7px
}

.panel.g{
    border:1px solid #9d51ff99
}

.panel.c{
    border:1px solid #f1c84b88
}

.pnrow{
    display:flex;
    justify-content:space-between;
    align-items:center;
    padding:2px 2px 6px
}

.pn{
    font-size:11px;
    font-weight:1000
}

.g .pn{
    color:#d5a8ff
}

.c .pn{
    color:#ffe37b
}

.cnt{
    font-size:7px;
    background:#ffffff12;
    border-radius:99px;
    padding:3px 5px
}


/* =====================================================
   KARTLAR
   ===================================================== */

.card{
    margin-bottom:4px;
    background:linear-gradient(
        145deg,
        #171d2df9,
        #0a0e17f9
    );
    border:1px solid #293448;
    border-radius:8px;
    padding:6px
}

.card:last-child{
    margin-bottom:0
}

.g .card{
    border-left:3px solid #9d51ff
}

.c .card{
    border-left:3px solid #f1c84b
}

.ur{
    display:flex;
    justify-content:space-between;
    align-items:center;
    gap:4px;
    margin-bottom:5px
}

.user{
    font-size:8px;
    font-weight:1000;
    word-break:break-word
}

.rank{
    font-size:6px;
    color:#77849a
}

.badge{
    padding:3px 5px;
    border-radius:5px;
    font-size:6px;
    font-weight:1000;
    background:#8d35ff;
    color:#fff
}

.c .badge{
    background:#f4d35e;
    color:#211700
}

.ig{
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:3px
}

.info{
    background:#ffffff09;
    border-radius:5px;
    padding:3px;
    font-size:5px;
    color:#818da1
}

.info b{
    display:block;
    color:#fff;
    font-size:8px;
    margin-top:1px;
    word-break:break-word
}

.go{
    display:block;
    text-align:center;
    color:#fff;
    text-decoration:none;
    background:#d71950;
    padding:5px;
    margin-top:5px;
    border-radius:6px;
    font-size:6px;
    font-weight:1000
}

.empty{
    text-align:center;
    padding:16px;
    color:#626e82;
    font-size:7px
}

.foot{
    text-align:center;
    color:#59647a;
    font-size:6px;
    padding:8px
}


/* =====================================================
   YENİ ANİMASYON
   ===================================================== */

.new{
    animation:in .7s ease-out
}

@keyframes in{

    0%{
        opacity:.3;
        transform:
            translateY(-7px)
            scale(.97)
    }

    40%{
        box-shadow:
            0 0 25px #9d51ff77
    }

    100%{
        opacity:1;
        transform:none
    }

}


/* =====================================================
   MOBİL
   ===================================================== */

@media(max-width:700px){

    body{
        padding:4px
    }

    .title{
        font-size:27px
    }

    .sub{
        font-size:8px
    }

    .status{
        font-size:7px
    }

    .grid{
        gap:4px
    }

    .maxbox,
    .panel{
        padding:5px;
        border-radius:9px
    }

    .max-title,
    .pn{
        font-size:8px
    }

    .max-user-name{
        font-size:7px
    }

    .max-coin{
        font-size:9px
    }

    .max-stat{
        font-size:4px
    }

    .max-stat b{
        font-size:7px
    }

    .card{
        padding:5px
    }

    .user{
        font-size:7px
    }

    .info{
        font-size:4px
    }

    .info b{
        font-size:7px
    }

    .badge{
        font-size:5px;
        padding:2px 4px
    }

    .go,
    .max-live{
        font-size:5px;
        padding:4px
    }

    .max-link{
        font-size:5px
    }

}

</style>

</head>

<body>

<div class="wrap">

<div class="head">

<div class="title">
🏆 ÖDÜL AVCISI
</div>

<div class="sub">
🟪 GOODY BAG • 🟨 HAZİNE SANDIĞI
</div>

<div id="status" class="status">
🟡 RADAR BAĞLANIYOR...
</div>

</div>


<div class="grid">


<!-- =================================================
     GOODY BAG SÜTUNU
     ================================================= -->

<div class="column">

<div class="maxbox g">

<div class="max-title">
🪙 EN YÜKSEK COIN • GOODY BAG
</div>

<div id="goodyMax"></div>

</div>


<div class="panel g">

<div class="pnrow">

<div class="pn">
🟪 GOODY BAG
</div>

<div id="gc" class="cnt">
0
</div>

</div>

<div id="gs"></div>

</div>

</div>


<!-- =================================================
     CHEST SÜTUNU
     ================================================= -->

<div class="column">

<div class="maxbox c">

<div class="max-title">
🪙 EN YÜKSEK COIN • HAZİNE SANDIĞI
</div>

<div id="chestMax"></div>

</div>


<div class="panel c">

<div class="pnrow">

<div class="pn">
🟨 HAZİNE SANDIĞI
</div>

<div id="cc" class="cnt">
0
</div>

</div>

<div id="cs"></div>

</div>

</div>

</div>


<div class="foot">
⚡ ÖDÜL AVCISI • CANLI RADAR
</div>

</div>


<script>

let data={
    goody_bags:[],
    chests:[]
};

let first=true;

const seenG=new Set();
const seenC=new Set();

const newG=new Set();
const newC=new Set();


/* =====================================================
   GÜVENLİ HTML
   ===================================================== */

const esc=v=>
String(v??"")
.replace(/&/g,"&amp;")
.replace(/</g,"&lt;")
.replace(/>/g,"&gt;")
.replace(/"/g,"&quot;")
.replace(/'/g,"&#039;");


/* =====================================================
   KEY
   ===================================================== */

const key=x=>
String(
    x.source_message_id ??
    x.room ??
    (
        (x.username??"")
        +"_"
        +(x.detected_at??"")
    )
);


/* =====================================================
   ZAMAN
   ===================================================== */

const time=x=>
Number(
    x.detected_at ??
    x.created_at ??
    x.timestamp ??
    0
);


/* =====================================================
   SON 5
   ===================================================== */

const five=a=>
Array.isArray(a)
?
[...a]
.sort(
    (a,b)=>time(b)-time(a)
)
.slice(0,5)
:
[];


/* =====================================================
   EN YÜKSEK COIN
   SADECE SON 5 KAYITTAN
   ===================================================== */

function renderMaxCoin(a,id,icon,type){

    const e=document.getElementById(id);

    const arr=five(a);

    if(!arr.length){

        e.innerHTML=
        '<div class="empty">⚡ Henüz veri yok.</div>';

        return;
    }


    const max=
        [...arr]
        .sort(
            (a,b)=>
            Number(b.coins||0)
            -
            Number(a.coins||0)
        )[0];


    const titleIcon=
        type==="G"
        ?"🟪"
        :"🟨";


    e.innerHTML=`

<div class="max-card">

<div class="max-user">

<div class="max-user-name">
${titleIcon} ${esc(max.username)}
</div>

<div class="max-coin">
🪙 ${esc(max.coins)}
</div>

</div>


<div class="max-stats">

<div class="max-stat">
🪙 COIN
<b>${esc(max.coins)}</b>
</div>

<div class="max-stat">
👥 KİŞİ
<b>${esc(max.people)}</b>
</div>

<div class="max-stat">
🙋 KATILAN
<b>${esc(max.joined)}</b>
</div>

<div class="max-stat">
📈 ORAN
<b>${esc(max.rate)}</b>
</div>

<div class="max-stat">
👀 İZLENME
<b>${esc(max.view)}</b>
</div>

<div class="max-stat">
🏠 ODA
<b>${esc(max.room)}</b>
</div>

</div>


${
max.live
?
`
<a
class="max-live"
href="${esc(max.live)}"
target="_blank"
rel="noopener"
>
🔴 TIKTOK CANLI YAYIN
</a>

<a
class="max-link"
href="${esc(max.live)}"
target="_blank"
rel="noopener"
>
${esc(max.live)}
</a>
`
:
""
}

</div>

`;

}


/* =====================================================
   SON KAYIT
   ===================================================== */

function last(x,id,icon,type){

    let e=document.getElementById(id);

    if(!x){

        e.innerHTML=
        '<div class="empty">⚡ Henüz veri yok.</div>';

        return;
    }

    let ns=
        type==="G"
        ?
        newG
        :
        newC;

    let k=key(x);

    e.innerHTML=`

<div class="lc">

<div class="lu">

<span>
${icon} ${esc(x.username)}
</span>

${
ns.has(k)
?
'<span class="badge">⚡ YENİ</span>'
:
""
}

</div>


<div class="stats">

<div class="st">
🪙 COIN
<b>${esc(x.coins)}</b>
</div>

<div class="st">
👥 KİŞİ
<b>${esc(x.people)}</b>
</div>

<div class="st">
🙋 KATILAN
<b>${esc(x.joined)}</b>
</div>

<div class="st">
📈 ORAN
<b>${esc(x.rate)}</b>
</div>

<div class="st">
👀 İZLENME
<b>${esc(x.view)}</b>
</div>

<div class="st">
🏠 ODA
<b>${esc(x.room)}</b>
</div>

</div>


${
x.live
?
`
<a
class="go"
href="${esc(x.live)}"
target="_blank"
rel="noopener"
>
🔴 CANLIYA GİT
</a>
`
:
""
}

</div>

`;

}


/* =====================================================
   LİSTE
   ===================================================== */

function list(a,id,cid,icon,type){

    let e=document.getElementById(id);

    let n=document.getElementById(cid);

    let arr=five(a);

    n.textContent=arr.length;


    if(!arr.length){

        e.innerHTML=
        '<div class="empty">⚡ Henüz veri yok.</div>';

        return;
    }


    let seen=
        type==="G"
        ?
        seenG
        :
        seenC;


    let ns=
        type==="G"
        ?
        newG
        :
        newC;


    arr.forEach(x=>{

        let k=key(x);

        if(first){

            seen.add(k);

        }else if(!seen.has(k)){

            seen.add(k);

            ns.add(k);

        }

    });


    e.innerHTML=

    arr.map((x,i)=>{

        let k=key(x);

        let fresh=ns.has(k);

        return `

<div class="card ${fresh?"new":""}">

<div class="ur">

<div class="user">
${icon} ${esc(x.username)}
</div>

${
fresh
?
'<div class="badge">⚡ YENİ</div>'
:
`<div class="rank">#${i+1}</div>`
}

</div>


<div class="ig">

<div class="info">
🪙 COIN
<b>${esc(x.coins)}</b>
</div>

<div class="info">
👥 KİŞİ
<b>${esc(x.people)}</b>
</div>

<div class="info">
🙋 KATILAN
<b>${esc(x.joined)}</b>
</div>

<div class="info">
📈 ORAN
<b>${esc(x.rate)}</b>
</div>

<div class="info">
👀 İZLENME
<b>${esc(x.view)}</b>
</div>

<div class="info">
🏠 ODA
<b>${esc(x.room)}</b>
</div>

</div>


${
x.live
?
`
<a
class="go"
href="${esc(x.live)}"
target="_blank"
rel="noopener"
>
🔴 CANLIYA GİT
</a>
`
:
""
}

</div>

`;

    }).join("");

}


/* =====================================================
   RADAR
   ===================================================== */

function render(){

    let g=five(data.goody_bags);

    let c=five(data.chests);


    /* GOODY EN YÜKSEK COIN */

    renderMaxCoin(
        data.goody_bags,
        "goodyMax",
        "🟪",
        "G"
    );


    /* CHEST EN YÜKSEK COIN */

    renderMaxCoin(
        data.chests,
        "chestMax",
        "🟨",
        "C"
    );


    /* GOODY */

    list(
        data.goody_bags,
        "gs",
        "gc",
        "🟪",
        "G"
    );


    /* CHEST */

    list(
        data.chests,
        "cs",
        "cc",
        "🟨",
        "C"
    );

}


/* =====================================================
   VERİ ÇEK
   ===================================================== */

async function load(){

    try{

        let r=await fetch(
            "/api/all?t="+Date.now(),
            {
                cache:"no-store"
            }
        );


        if(!r.ok){
            throw Error(r.status);
        }


        let d=await r.json();


        data={
            goody_bags:
                Array.isArray(d.goody_bags)
                ?
                d.goody_bags
                :
                [],

            chests:
                Array.isArray(d.chests)
                ?
                d.chests
                :
                []
        };


        let s=
            document.getElementById("status");


        s.className="status";


        s.textContent=
            "🟢 RADAR AKTİF • CANLI VERİ";


        render();


        first=false;


    }catch(e){

        let s=
            document.getElementById("status");


        s.className=
            "status error";


        s.textContent=
            "🔴 VERİ BAĞLANTISI HATASI";


        console.error(e);

    }

}


setInterval(
    load,
    2000
);


load();

</script>

</body>
</html>
"""


# =========================================================
# HTTP
# =========================================================

async def radar_page(request):

    return web.Response(
        text=RADAR_HTML,
        content_type="text/html",
        charset="utf-8"
    )


@web.middleware
async def cors(request,handler):

    if request.method=="OPTIONS":

        return web.Response(
            status=204,
            headers={
                "Access-Control-Allow-Origin":"*",
                "Access-Control-Allow-Methods":
                    "GET, OPTIONS",
                "Access-Control-Allow-Headers":"*"
            }
        )


    r=await handler(request)


    r.headers[
        "Access-Control-Allow-Origin"
    ]="*"

    r.headers[
        "Access-Control-Allow-Methods"
    ]="GET, OPTIONS"

    r.headers[
        "Access-Control-Allow-Headers"
    ]="*"


    return r


async def api_all(request):

    return web.json_response({

        "status":"online",

        "server_time":int(time.time()),

        "chests":
            list(LIVE_CHESTS.values()),

        "goody_bags":
            list(LIVE_GOODY_BAGS.values())

    })


async def api_boxes(request):

    return web.json_response(
        list(LIVE_CHESTS.values())
    )


async def api_goody(request):

    return web.json_response(
        list(LIVE_GOODY_BAGS.values())
    )


async def api_status(request):

    return web.json_response({

        "status":"online",

        "chests":
            len(LIVE_CHESTS),

        "goody_bags":
            len(LIVE_GOODY_BAGS),

        "server_time":
            int(time.time())

    })


# =========================================================
# HTTP SERVER
# =========================================================

async def start_http():

    app=web.Application(
        middlewares=[cors]
    )


    app.router.add_get(
        "/",
        radar_page
    )

    app.router.add_get(
        "/radar",
        radar_page
    )


    app.router.add_get(
        "/api/all",
        api_all
    )

    app.router.add_get(
        "/api/boxes",
        api_boxes
    )

    app.router.add_get(
        "/api/goody_bags",
        api_goody
    )

    app.router.add_get(
        "/api/status",
        api_status
    )


    for p in [
        "/api/all",
        "/api/boxes",
        "/api/goody_bags",
        "/api/status"
    ]:

        app.router.add_options(
            p,
            lambda request:
                web.Response(status=204)
        )


    runner=web.AppRunner(app)

    await runner.setup()

    await web.TCPSite(
        runner,
        "0.0.0.0",
        PORT
    ).start()


    print(
        "[HTTP] Sunucu başladı:",
        PORT
    )


# =========================================================
# TELEGRAM LISTENER
# =========================================================

async def listener(event):

    try:

        k=(
            event.chat_id,
            event.message.id
        )


        if k in processed_messages:
            return


        processed_messages.add(k)


        if len(processed_messages)>50000:
            processed_messages.clear()


        d=parse(event)


        if d and add(d):

            await telegram_queue.put(d)


    except Exception as e:

        print(
            "[DİNLEYİCİ HATASI]",
            repr(e)
        )


# =========================================================
# MAIN
# =========================================================

async def main():

    global http_session


    print(
        "🏆 ÖDÜL AVCISI BAŞLIYOR"
    )


    http_session=aiohttp.ClientSession()


    await start_http()


    client=TelegramClient(
        StringSession(STRING_SESSION),
        API_ID,
        API_HASH
    )


    await client.start()


    print(
        "[TELEGRAM] İstemci bağlandı."
    )


    client.add_event_handler(
        listener,
        events.NewMessage(
            chats=SOURCE_CHATS
        )
    )


    asyncio.create_task(
        sender()
    )


    print(
        "[HAZIR] Goody Bag + Hazine Sandığı aktif."
    )

    print(
        "[HAZIR] Goody + Chest üstünde ayrı EN YÜKSEK COIN aktif."
    )

    print(
        "[HAZIR] Son 5 kayıt gösteriliyor."
    )

    print(
        "[HAZIR] Yeni gelen HER kayıt ⚡ YENİ."
    )

    print(
        "[HAZIR] Telegram linkleri butonsuz normal link."
    )


    try:

        await client.run_until_disconnected()


    finally:

        if http_session:
            await http_session.close()


# =========================================================
# START
# =========================================================

if __name__=="__main__":

    try:

        asyncio.run(main())

    except KeyboardInterrupt:

        print("Kapatıldı.")

    except Exception as e:

        print(
            "[KRİTİK HATA]",
            repr(e)
        )
