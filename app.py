import asyncio
import time
import httpx
import json
import traceback
from collections import defaultdict
from flask import Flask, request, jsonify
from flask_cors import CORS
from cachetools import TTLCache
from typing import Tuple
from proto import FreeFire_pb2, main_pb2, AccountPersonalShow_pb2
from google.protobuf import json_format, message
from google.protobuf.message import Message
from Crypto.Cipher import AES
import base64

# === Settings ===
MAIN_KEY = base64.b64decode('WWcmdGMlREV1aDYlWmNeOA==')
MAIN_IV = base64.b64decode('Nm95WkRyMjJFM3ljaGpNJQ==')
RELEASEVERSION = "OB52"
USERAGENT = "Dalvik/2.1.0 (Linux; U; Android 13; CPH2095 Build/RKQ1.211119.001)"
SUPPORTED_REGIONS = {"IND", "BR", "US", "SAC", "NA", "SG", "RU", "ID", "TW", "VN", "TH", "ME", "PK", "CIS", "BD", "EU"}

# === Flask App Setup ===
app = Flask(__name__)
CORS(app)
cache = TTLCache(maxsize=100, ttl=300)
cached_tokens = defaultdict(dict)

# === Helper Functions ===
def pad(text: bytes) -> bytes:
    padding_length = AES.block_size - (len(text) % AES.block_size)
    return text + bytes([padding_length] * padding_length)

def aes_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    aes = AES.new(key, AES.MODE_CBC, iv)
    return aes.encrypt(pad(plaintext))

def decode_protobuf(encoded_data: bytes, message_type: message.Message) -> message.Message:
    instance = message_type()
    instance.ParseFromString(encoded_data)
    return instance

async def json_to_proto(json_data: str, proto_message: Message) -> bytes:
    json_format.ParseDict(json.loads(json_data), proto_message)
    return proto_message.SerializeToString()

def get_account_credentials(region: str) -> str:
    r = region.upper()
    if r == "IND":
        return "uid=4411404515&password=FFSK_65UB8_BY_SPIDEERIO_GAMING_RJ6J7"
    elif r == "BD":
        return "uid=4490907300&password=FFSK_OK8N9_BY_SPIDEERIO_GAMING_9780Y"
    elif r in {"BR", "US", "SAC", "NA"}:
        return "uid=4674233040&password=BRAZIL_7J45M_BY_STAR_GMR_Z0ZJZ"
    else:
        return "uid=4674239440&password=RUSSS_4PPA4_BY_STAR_GMR_KY4U2"

# === Token Generation ===
async def get_access_token(account: str):
    url = "https://ffmconnect.live.gop.garenanow.com/oauth/guest/token/grant"
    payload = account + "&response_type=token&client_type=2&client_secret=2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3&client_id=100067"
    headers = {'User-Agent': USERAGENT, 'Connection': "Keep-Alive", 'Accept-Encoding': "gzip", 'Content-Type': "application/x-www-form-urlencoded"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, data=payload, headers=headers)
        data = resp.json()
        return data.get("access_token", "0"), data.get("open_id", "0")

async def create_jwt(region: str):
    try:
        account = get_account_credentials(region)
        token_val, open_id = await get_access_token(account)
        
        body = json.dumps({"open_id": open_id, "open_id_type": "4", "login_token": token_val, "orign_platform_type": "4"})
        proto_bytes = await json_to_proto(body, FreeFire_pb2.LoginReq())
        payload = aes_cbc_encrypt(MAIN_KEY, MAIN_IV, proto_bytes)
        
        url = "https://loginbp.ggblueshark.com/MajorLogin"
        headers = {
            'User-Agent': USERAGENT, 'Connection': "Keep-Alive", 'Accept-Encoding': "gzip",
            'Content-Type': "application/octet-stream", 'Expect': "100-continue",
            'X-Unity-Version': "2018.4.11f1", 'X-GA': "v1 1", 'ReleaseVersion': RELEASEVERSION
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, data=payload, headers=headers)
            msg = json.loads(json_format.MessageToJson(decode_protobuf(resp.content, FreeFire_pb2.LoginRes)))
            
            cached_tokens[region] = {
                'token': f"Bearer {msg.get('token','0')}",
                'region': msg.get('lockRegion','0'),
                'server_url': msg.get('serverUrl','0'),
                'expires_at': time.time() + 25200
            }
            if msg.get('token', '0') == '0':
                print(f"[{region}] FAILED - Garena blocked/rate-limited the guest account login.")
            else:
                print(f"[{region}] SUCCESS - JWT Loaded.")
                
    except Exception as e:
        print(f"[{region}] Failed to generate JWT: {e}")

async def initialize_tokens():
    sem = asyncio.Semaphore(5)
    
    async def task_with_sem(r):
        async with sem:
            await create_jwt(r)

    tasks = [task_with_sem(r) for r in SUPPORTED_REGIONS]
    await asyncio.gather(*tasks)

async def refresh_tokens_periodically():
    while True:
        await asyncio.sleep(25200)
        await initialize_tokens()

async def get_token_info(region: str) -> Tuple[str, str, str]:
    info = cached_tokens.get(region)
    if info and time.time() < info['expires_at']:
        return info['token'], info['region'], info['server_url']
    await create_jwt(region)
    info = cached_tokens.get(region)
    if not info:
        raise ValueError(f"Failed to fetch token information for region {region}")
    return info['token'], info['region'], info['server_url']

async def get_region_by_uid(uid: str) -> str:
    """Fetch player region using external API"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        # NOTE: If this URL is a dummy placeholder, it WILL fail.
        url = f"https://crownx-region-api.vercel.app/region?uid={uid}"
        resp = await client.get(url)
        if resp.status_code != 200:
            raise ValueError(f"External Region API returned {resp.status_code}")
        data = resp.json()
        return data.get("region", "").upper()

async def GetAccountInformation(uid, unk, region, endpoint):
    # Ensure UID and unk are passed as integers for Protobuf compatibility
    payload_dict = {'a': int(uid), 'b': int(unk)}
    payload = await json_to_proto(json.dumps(payload_dict), main_pb2.GetPlayerPersonalShow())
    data_enc = aes_cbc_encrypt(MAIN_KEY, MAIN_IV, payload)
    
    token, lock, server = await get_token_info(region)
    
    # Catch empty server token
    if server == "0":
        raise ValueError(f"Server URL missing for region {region}. Garena might have blocked the Auth Token.")

    headers = {
        'User-Agent': USERAGENT, 'Connection': "Keep-Alive", 'Accept-Encoding': "gzip",
        'Content-Type': "application/octet-stream",
        'Authorization': token, 'X-Unity-Version': "2018.4.11f1", 'X-GA': "v1 1",
        'ReleaseVersion': RELEASEVERSION
    }
    
    # Prevent double slashes in URL which causes HTTP 400 Bad Request
    url = f"{server.rstrip('/')}/{endpoint.lstrip('/')}"
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Use content=data_enc instead of data=data_enc for raw bytes
        resp = await client.post(url, content=data_enc, headers=headers)
        if resp.status_code != 200:
            raise ValueError(f"Garena API returned HTTP {resp.status_code} - Details: {resp.text}")
        
        return json.loads(json_format.MessageToJson(decode_protobuf(resp.content, AccountPersonalShow_pb2.AccountPersonalShowInfo)))

def format_response(data):
    return {
        "AccountInfo": {
            "AccountAvatarId": data.get("basicInfo", {}).get("headPic"),
            "AccountBPBadges": data.get("basicInfo", {}).get("badgeCnt"),
            "AccountBPID": data.get("basicInfo", {}).get("badgeId"),
            "AccountBannerId": data.get("basicInfo", {}).get("bannerId"),
            "AccountCreateTime": data.get("basicInfo", {}).get("createAt"),
            "AccountEXP": data.get("basicInfo", {}).get("exp"),
            "AccountLastLogin": data.get("basicInfo", {}).get("lastLoginAt"),
            "AccountLevel": data.get("basicInfo", {}).get("level"),
            "AccountLikes": data.get("basicInfo", {}).get("liked"),
            "AccountName": data.get("basicInfo", {}).get("nickname"),
            "AccountRegion": data.get("basicInfo", {}).get("region"),
            "AccountSeasonId": data.get("basicInfo", {}).get("seasonId"),
            "AccountType": data.get("basicInfo", {}).get("accountType"),
            "BrMaxRank": data.get("basicInfo", {}).get("maxRank"),
            "BrRankPoint": data.get("basicInfo", {}).get("rankingPoints"),
            "CsMaxRank": data.get("basicInfo", {}).get("csMaxRank"),
            "CsRankPoint": data.get("basicInfo", {}).get("csRankingPoints"),
            "EquippedWeapon": data.get("basicInfo", {}).get("weaponSkinShows", []),
            "ReleaseVersion": data.get("basicInfo", {}).get("releaseVersion"),
            "ShowBrRank": data.get("basicInfo", {}).get("showBrRank"),
            "ShowCsRank": data.get("basicInfo", {}).get("showCsRank"),
            "Title": data.get("basicInfo", {}).get("title")
        },
        "AccountProfileInfo": {
            "EquippedOutfit": data.get("profileInfo", {}).get("clothes", []),
            "EquippedSkills": data.get("profileInfo", {}).get("equipedSkills", [])
        },
        "GuildInfo": {
            "GuildCapacity": data.get("clanBasicInfo", {}).get("capacity"),
            "GuildID": str(data.get("clanBasicInfo", {}).get("clanId")),
            "GuildLevel": data.get("clanBasicInfo", {}).get("clanLevel"),
            "GuildMember": data.get("clanBasicInfo", {}).get("memberNum"),
            "GuildName": data.get("clanBasicInfo", {}).get("clanName"),
            "GuildOwner": str(data.get("clanBasicInfo", {}).get("captainId"))
        },
        "captainBasicInfo": data.get("captainBasicInfo", {}),
        "creditScoreInfo": data.get("creditScoreInfo", {}),
        "petInfo": data.get("petInfo", {}),
        "socialinfo": data.get("socialInfo", {})
    }

# === API Routes ===
@app.route('/info')
async def get_account_info():
    uid = request.args.get('uid')
    region = request.args.get('region')
    
    if not uid:
        return jsonify({"error": "Please provide UID."}), 400
    
    try:
        # Fallback if region is missing in URL arguments
        if not region:
            try:
                region = await get_region_by_uid(uid)
            except Exception as reg_err:
                return jsonify({
                    "error": "Failed to auto-detect region. Please pass region in URL like: &region=IND",
                    "details": str(reg_err)
                }), 400
        
        region = region.upper()
        if region not in SUPPORTED_REGIONS:
            return jsonify({"error": "Invalid region or unsupported region"}), 400
        
        # Get account information
        return_data = await GetAccountInformation(uid, "7", region, "/GetPlayerPersonalShow")
        formatted = format_response(return_data)
        return jsonify(formatted), 200
    
    except ValueError as ve:
        err_msg = str(ve)
        # Handle Account Not Found cleanly
        if "ACCOUNT_NOT_FOUND" in err_msg:
            return jsonify({
                "error": f"Account not found in region '{region}'. Please check the UID or provide the correct region.",
                "details": err_msg
            }), 404
        return jsonify({"error": f"Server processing error: {err_msg}"}), 500
    except Exception as e:
        # Print actual error to terminal
        traceback.print_exc()
        return jsonify({"error": f"Server processing error: {str(e)}"}), 500

@app.route('/refresh', methods=['GET', 'POST'])
def refresh_tokens_endpoint():
    try:
        asyncio.run(initialize_tokens())
        return jsonify({'message': 'Tokens refreshed for all regions.'}), 200
    except Exception as e:
        return jsonify({'error': f'Refresh failed: {e}'}), 500

# === Startup ===
async def startup():
    await initialize_tokens()
    asyncio.create_task(refresh_tokens_periodically())

if __name__ == '__main__':
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(startup())
    app.run(host='0.0.0.0', port=5080, debug=True)