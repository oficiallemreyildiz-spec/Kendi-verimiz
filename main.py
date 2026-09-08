import os,re,json,base64,asyncio,time
from urllib.parse import unquote

import aiohttp
from aiohttp import web
from telethon import TelegramClient,events
from telethon.sessions import StringSession


# =========================================================
# AYARLAR
# =========================================================

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


# =========================================================
# VERİLER
# =========================================================

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
# TOKEN BUL
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


    # Telegram entities

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


# =========================================================
# TOKEN ÇÖZ
# =========================================================

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
# ROOM BUL
# =========================================================

def room_from_text(t):

    patterns=[
        r'https?://live\.dichvu321\.com/t/\?p=([A-Za-z0-9_\-+/=]+)',
        r'https?://[^ \n]+/t/\?p=([A-Za-z0-9_\-+/=]+)'
    ]

    for p in patterns:

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

    patterns=[
        r'^\s*##\s*T\d+\s*[›>:]\s*([^\s\n]+)',
        r'^\s*T\d+\s*[›>:]\s*([^\s\n]+)'
    ]

    for p in patterns:

        m=re.search(p,t or "",re.M)

        if m:
            return m.group(1).strip()

    return None


# =========================================================
# COIN
# =========================================================

def coins(t,d=None):

    patterns=[
        r'(?:TÚI|TUI)\s*:\s*(\d+)\s*/',
        r'BOX\s*:\s*(\d+)\s*/',
        r'(\d+)\s*/\s*(\d+)'
    ]

    for p in patterns:

        m=re.search(p,t or "",re.I)

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
# KİŞİ
# =========================================================

def people(t):

    patterns=[
        r'(?:TÚI|TUI)\s*:\s*\d+\s*/\s*(\d+)',
        r'BOX\s*:\s*\d+\s*/\s*(\d+)',
        r'(\d+)\s*/\s*(\d+)'
    ]

    for p in patterns:

        m=re.search(p,t or "",re.I)

        if m:

            if "TÚI" in p or "TUI" in p or "BOX" in p:
                return safe_int(m.group(1))

            return safe_int(m.group(2))

    return 0


# =========================================================
# JOINED
# =========================================================

def joined(t):

    patterns=[
        r'Đã\s*join\s*:\s*(\d+)',
        r'joined\s*:\s*(\d+)',
        r'join\s*:\s*(\d+)'
    ]

    for p in patterns:

        m=re.search(p,t or "",re.I)

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


    for k in ["ratio","rate"]:

        if d and k in d:
            return safe_float(d[k])

    return 0


# =========================================================
# TÜR BELİRLE
# =========================================================

def is_goody(t,d):

    u=(t or "").upper()

    # Goody öncelikli

    if re.search(
        r'TÚI|TUI|GOODY\s*BAG|REWARD\s*BAG',
        u
    ):
        return True


    # Chest

    if re.search(
        r'\bBOX\b|RƯƠNG|TREO|HAZİNE',
        u
    ) or "🟡" in t:

        return False


    if d and d.get("is_goody_bag") in [
        True,
        1,
        "1",
        "true",
        "True"
    ]:
        return True


    if d and d.get("is_goody_bag") in [
        False,
        0,
        "0",
        "false",
        "False"
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
            +
            safe_int(m.group(1))*60
            +
            safe_int(m.group(2))
        )


    return now+180


# =========================================================
# PARSE
# =========================================================

def parse(event):

    t=event.message.raw_text or ""

    token=token_from_event(event)

    d=decode_token(token)

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


    # VIEW

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


    if not live:

        live=f"https://www.tiktok.com/share/live/{r}"


    return {

        "type":
            "GOODY BAG"
            if g
            else
            "CHEST",

        "box_name":
            "Goody Bag"
            if g
            else
            "Hazine Sandığı",

        "username":u,

        "coins":
            coins(t,d),

        "people":
            p,

        "joined":
            j,

        "rate":
            rate(t,d),

        "view":
            v,

        "room":
            r,

        "live":
            live,

        "target_time":
            target_time(t,d),

        "detected_at":
            int(time.time()),

        "source_message_id":
            event.message.id
    }


# =========================================================
# RADARA EKLE
# =========================================================

def add(d):

    if not d or not d.get("room"):
        return False


    target=(
        LIVE_GOODY_BAGS
        if d["type"]=="GOODY BAG"
        else
        LIVE_CHESTS
    )


    r=d["room"]


    # Aynı kaydı çok kısa sürede tekrar ekleme

    if (
        r in target
        and
        int(time.time())
        -
        safe_int(
            target[r].get("detected_at")
        )
        <
        5
    ):
        return False


    target[r]=d


    print(
        "[RADAR]",
        d["type"],
        d["username"],
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


    if d["type"]=="GOODY BAG":

        title="🟪 GOODY BAG"

    else:

        title="🟨 HAZİNE SANDIĞI"


    text=(
        f"{title}\n\n"
        f"👤 Kullanıcı: {d['username']}\n"
        f"🪙 Coin: {d['coins']}\n"
        f"👥 Kişi: {d['people']}\n"
        f"🙋 Katılan: {d['joined']}\n"
        f"📈 Oran: {d['rate']}\n"
        f"👀 İzlenme: {d['view']}\n"
    )


    # DOĞRUDAN URL

    if d.get("live"):

        text += (
            "\n🔴 "
            f"{d['live']}"
        )


    url=(
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )


    for attempt in range(1,9):

        try:

            payload={
                "chat_id":TARGET_CHAT_ID,
                "text":text,
                "disable_web_page_preview":True
            }


            async with http_session.post(
                url,
                json=payload
            ) as response:

                response_text=await response.text()


                if response.status==200:

                    return


                # Telegram rate limit

                if response.status==429:

                    try:

                        wait=json.loads(
                            response_text
                        ).get(
                            "parameters",
                            {}
                        ).get(
                            "retry_after",
                            30
                        )

                    except:

                        wait=30


                    print(
                        "[TELEGRAM 429]",
                        "Bekleniyor:",
                        wait
                    )


                    await asyncio.sleep(
                        max(
                            1,
                            safe_int(wait,30)
                        )
                    )

                    continue


                # Geçici Telegram hataları

                if response.status in [
                    500,
                    502,
                    503,
                    504
                ]:

                    await asyncio.sleep(
                        min(
                            5*attempt,
                            30
                        )
                    )

                    continue


                print(
                    "[TELEGRAM HATA]",
                    response.status,
                    response_text
                )

                return


        except Exception as e:

            print(
                "[TELEGRAM]",
                repr(e)
            )

            await asyncio.sleep(
                min(
                    5*attempt,
                    30
                )
            )


# =========================================================
# TELEGRAM QUEUE
# =========================================================

async def sender():

    while True:

        d=await telegram_queue.get()

        try:

            await send_tg(d)

        finally:

            telegram_queue.task_done()


# =========================================================
# WEB SAYFASI
# =========================================================

RADAR_HTML = r"""
<!doctype html>

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
    box-sizing:border-box;
}


body{
    margin:0;
    padding:7px;
    background:#05060c;
    color:#fff;
    font-family:Arial,sans-serif;
}


.wrap{
    max-width:1200px;
    margin:auto;
}


.head{
    text-align:center;
    padding:7px 4px 10px;
}


.title{
    font-size:clamp(25px,7vw,48px);
    font-weight:1000;
    text-shadow:
        0 0 5px #fff,
        0 0 16px #8b55ff,
        0 0 35px #5d25ff;
}


.sub{
    font-size:11px;
    color:#aeb5c8;
    font-weight:800;
    margin-top:6px;
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
    font-weight:900;
}


/* =====================================================
   EN ÖNEMLİ KISIM:
   HER ZAMAN YAN YANA
   ===================================================== */

.last,
.grid{

    display:grid;

    grid-template-columns:
        repeat(2,minmax(0,1fr));

    gap:8px;

    width:100%;
}


.last{
    margin-bottom:9px;
}


.box{

    min-width:0;

    background:#090c15ed;

    border:1px solid #293448;

    border-radius:13px;

    padding:7px;

}


.g{
    border-color:#9d51ff99;
}


.c{
    border-color:#f1c84b88;
}


.lt{

    font-size:10px;

    font-weight:1000;

    margin-bottom:5px;

}


.g .lt,
.g .pn{

    color:#d5a8ff;

}


.c .lt,
.c .pn{

    color:#ffe37b;

}


.lc,
.card{

    background:#171d2df5;

    border:1px solid #293448;

    border-radius:8px;

    padding:6px;

}


.lc{

    border-left:3px solid #9d51ff;

}


.c .lc{

    border-left-color:#f1c84b;

}


.lu,
.ur{

    display:flex;

    justify-content:space-between;

    align-items:center;

    gap:4px;

    font-size:9px;

    font-weight:1000;

    margin-bottom:5px;

}


.stats,
.ig{

    display:grid;

    grid-template-columns:1fr 1fr;

    gap:3px;

}


.st,
.info{

    background:#ffffff09;

    border-radius:5px;

    padding:3px;

    font-size:5px;

    color:#818da1;

}


.st b,
.info b{

    display:block;

    color:#fff;

    font-size:8px;

    margin-top:1px;

}


.pnrow{

    display:flex;

    justify-content:space-between;

    align-items:center;

    padding:2px 2px 6px;

}


.pn{

    font-size:11px;

    font-weight:1000;

}


.cnt{

    font-size:7px;

    background:#ffffff12;

    border-radius:99px;

    padding:3px 5px;

}


.card{

    margin-bottom:4px;

    background:
        linear-gradient(
            145deg,
            #171d2df9,
            #0a0e17f9
        );

}


.card:last-child{
    margin-bottom:0;
}


.g .card{
    border-left:3px solid #9d51ff;
}


.c .card{
    border-left:3px solid #f1c84b;
}


.user{

    font-size:8px;

    font-weight:1000;

    word-break:break-word;

}


.rank{

    font-size:6px;

    color:#77849a;

}


.new{

    animation:
        in .7s ease-out;

}


.badge{

    padding:3px 5px;

    border-radius:5px;

    font-size:6px;

    font-weight:1000;

    background:#8d35ff;

    color:#fff;

}


.c .badge{

    background:#f4d35e;

    color:#211700;

}


/* =====================================================
   DOĞRUDAN CANLI LINK
   ===================================================== */

.live-link{

    display:block;

    margin-top:6px;

    padding:5px;

    border-radius:6px;

    background:#ffffff08;

    border:1px solid #ffffff12;

    color:#8fc7ff;

    text-decoration:none;

    font-size:6px;

    font-weight:900;

    word-break:break-all;

    line-height:1.4;

}


.live-link:hover{

    background:#ffffff12;

    text-decoration:underline;

}


@keyframes in{

    0%{

        opacity:.3;

        transform:
            translateY(-7px)
            scale(.97);

    }

    40%{

        box-shadow:
            0 0 25px #9d51ff77;

    }

    100%{

        opacity:1;

        transform:none;

    }

}


.empty{

    text-align:center;

    padding:16px;

    color:#626e82;

    font-size:7px;

}


.foot{

    text-align:center;

    color:#59647a;

    font-size:6px;

    padding:8px;

}


/* =====================================================
   MOBİLDE DE YAN YANA
   ===================================================== */

@media(max-width:700px){

    body{
        padding:4px;
    }


    .title{
        font-size:27px;
    }


    .sub{
        font-size:8px;
    }


    .status{
        font-size:7px;
    }


    .last,
    .grid{

        grid-template-columns:
            repeat(2,minmax(0,1fr));

        gap:4px;

    }


    .box{

        padding:5px;

        border-radius:9px;

    }


    .lt{
        font-size:7px;
    }


    .pn{
        font-size:8px;
    }


    .cnt{
        font-size:6px;
    }


    .card,
    .lc{
        padding:5px;
    }


    .user{
        font-size:7px;
    }


    .info,
    .st{
        font-size:4px;
    }


    .info b,
    .st b{
        font-size:7px;
    }


    .badge{

        font-size:5px;

        padding:2px 4px;

    }


    .live-link{

        font-size:5px;

        padding:4px;

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

    <div
        id="status"
        class="status"
    >
        🟡 RADAR BAĞLANIYOR...
    </div>

</div>


<!-- =====================================================
     SON YAKALANANLAR
     ===================================================== -->

<div class="last">


    <div class="box g">

        <div class="lt">
            ⚡ SON GOODY BAG
        </div>

        <div id="lg"></div>

    </div>


    <div class="box c">

        <div class="lt">
            ⚡ SON HAZİNE SANDIĞI
        </div>

        <div id="lc"></div>

    </div>


</div>


<!-- =====================================================
     ANA LİSTELER
     ===================================================== -->

<div class="grid">


    <div class="box g">

        <div class="pnrow">

            <div class="pn">
                🟪 GOODY BAG
            </div>

            <div
                id="gc"
                class="cnt"
            >
                0
            </div>

        </div>

        <div id="gs"></div>

    </div>


    <div class="box c">

        <div class="pnrow">

            <div class="pn">
                🟨 HAZİNE SANDIĞI
            </div>

            <div
                id="cc"
                class="cnt"
            >
                0
            </div>

        </div>

        <div id="cs"></div>

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


/* Yeni kayıtlar oturum boyunca burada tutulur */

const seenG=new Set();

const seenC=new Set();

const newG=new Set();

const newC=new Set();


/* HTML güvenliği */

const esc=v=>
    String(v??"")
        .replace(/&/g,"&amp;")
        .replace(/</g,"&lt;")
        .replace(/>/g,"&gt;")
        .replace(/"/g,"&quot;")
        .replace(/'/g,"&#039;");


/* Kayıt anahtarı */

const key=x=>
    String(
        x.source_message_id
        ??
        x.room
        ??
        (
            (x.username??"")
            +
            "_"
            +
            (x.detected_at??"")
        )
    );


/* Tarih */

const time=x=>
    Number(
        x.detected_at
        ||
        x.created_at
        ||
        x.timestamp
        ||
        0
    );


/* Her bölümde son 5 */

const five=a=>
    Array.isArray(a)
        ?
        [...a]
            .sort(
                (a,b)=>
                    time(b)-time(a)
            )
            .slice(0,5)
        :
        [];


/* =====================================================
   SON KAYIT
   ===================================================== */

function last(x,id,icon,type){

    let e=
        document.getElementById(id);


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
                    ${icon}
                    ${esc(x.username)}
                </span>

                ${
                    ns.has(k)
                    ?
                    '<span class="badge">⚡ YENİ</span>'
                    :
                    ''
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
                    class="live-link"
                    href="${esc(x.live)}"
                    target="_blank"
                    rel="noopener noreferrer"
                >
                    ${esc(x.live)}
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

    let e=
        document.getElementById(id);


    let n=
        document.getElementById(cid);


    let arr=five(a);


    n.textContent=
        arr.length;


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


    /* Yeni kayıt kontrolü */

    arr.forEach(x=>{

        let k=key(x);


        if(first){

            /* İlk açılışta eski kayıtlar YENİ değil */

            seen.add(k);

        }

        else if(!seen.has(k)){

            seen.add(k);

            ns.add(k);

        }

    });


    e.innerHTML=
        arr
            .map(
                (x,i)=>{

                    let k=key(x);

                    let fresh=
                        ns.has(k);


                    return `

                    <div
                        class="card ${fresh?"new":""}"
                    >


                        <div class="ur">


                            <div class="user">

                                ${icon}

                                ${esc(x.username)}

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

                                <b>
                                    ${esc(x.coins)}
                                </b>

                            </div>


                            <div class="info">

                                👥 KİŞİ

                                <b>
                                    ${esc(x.people)}
                                </b>

                            </div>


                            <div class="info">

                                🙋 KATILAN

                                <b>
                                    ${esc(x.joined)}
                                </b>

                            </div>


                            <div class="info">

                                📈 ORAN

                                <b>
                                    ${esc(x.rate)}
                                </b>

                            </div>


                            <div class="info">

                                👀 İZLENME

                                <b>
                                    ${esc(x.view)}
                                </b>

                            </div>


                            <div class="info">

                                🏠 ODA

                                <b>
                                    ${esc(x.room)}
                                </b>

                            </div>


                        </div>


                        ${
                            x.live
                            ?
                            `
                            <a
                                class="live-link"
                                href="${esc(x.live)}"
                                target="_blank"
                                rel="noopener noreferrer"
                            >
                                ${esc(x.live)}
                            </a>
                            `
                            :
                            ""
                        }


                    </div>

                    `;

                }
            )
            .join("");

}


/* =====================================================
   RENDER
   ===================================================== */

function render(){

    let g=
        five(data.goody_bags);


    let c=
        five(data.chests);


    /* Üstte son kayıtlar */

    last(
        g[0],
        "lg",
        "🟪",
        "G"
    );


    last(
        c[0],
        "lc",
        "🟨",
        "C"
    );


    /* Alt listeler */

    list(
        data.goody_bags,
        "gs",
        "gc",
        "🟪",
        "G"
    );


    list(
        data.chests,
        "cs",
        "cc",
        "🟨",
        "C"
    );

}


/* =====================================================
   API'DEN VERİ AL
   ===================================================== */

async function load(){

    try{

        let r=
            await fetch(
                "/api/all?t="+Date.now(),
                {
                    cache:"no-store"
                }
            );


        if(!r.ok){

            throw Error(r.status);

        }


        let d=
            await r.json();


        data={

            goody_bags:
                Array.isArray(
                    d.goody_bags
                )
                ?
                d.goody_bags
                :
                [],


            chests:
                Array.isArray(
                    d.chests
                )
                ?
                d.chests
                :
                []

        };


        let s=
            document.getElementById(
                "status"
            );


        s.className="status";


        s.textContent=
            "🟢 RADAR AKTİF • CANLI VERİ";


        render();


        first=false;


    }catch(e){

        let s=
            document.getElementById(
                "status"
            );


        s.className=
            "status error";


        s.textContent=
            "🔴 VERİ BAĞLANTISI HATASI";


        console.error(e);

    }

}


/* 2 saniyede bir güncelle */

setInterval(
    load,
    2000
);


/* İlk yükleme */

load();


</script>


</body>

</html>
"""


# =========================================================
# WEB
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


    response=await handler(request)


    response.headers[
        "Access-Control-Allow-Origin"
    ]="*"


    response.headers[
        "Access-Control-Allow-Methods"
    ]="GET, OPTIONS"


    response.headers[
        "Access-Control-Allow-Headers"
    ]="*"


    return response


# =========================================================
# API
# =========================================================

async def api_all(request):

    return web.json_response({

        "status":"online",

        "server_time":
            int(time.time()),

        "chests":
            list(
                LIVE_CHESTS.values()
            ),

        "goody_bags":
            list(
                LIVE_GOODY_BAGS.values()
            )

    })


async def api_boxes(request):

    return web.json_response(
        list(
            LIVE_CHESTS.values()
        )
    )


async def api_goody(request):

    return web.json_response(
        list(
            LIVE_GOODY_BAGS.values()
        )
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


    for path in [
        "/api/all",
        "/api/boxes",
        "/api/goody_bags",
        "/api/status"
    ]:

        app.router.add_options(
            path,
            lambda request:
                web.Response(status=204)
        )


    runner=
        web.AppRunner(app)


    await runner.setup()


    site=
        web.TCPSite(
            runner,
            "0.0.0.0",
            PORT
        )


    await site.start()


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


        if d:

            added=add(d)


            if added:

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


    http_session=
        aiohttp.ClientSession()


    await start_http()


    client=
        TelegramClient(
            StringSession(
                STRING_SESSION
            ),
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
        "[HAZIR] Son Goody + Son Chest üstte."
    )


    print(
        "[HAZIR] Yeni gelen HER kayıt ⚡ YENİ."
    )


    try:

        await client.run_until_disconnected()


    finally:

        if http_session:

            await http_session.close()


# =========================================================
# ÇALIŞTIR
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
