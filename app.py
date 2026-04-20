import asyncio
import time
import httpx
import json
import base64
from collections import defaultdict
from flask import Flask, request, jsonify
from flask_cors import CORS
from cachetools import TTLCache
from typing import Tuple
from proto import FreeFire_pb2, main_pb2, AccountPersonalShow_pb2
from google.protobuf import json_format
from google.protobuf.message import Message
from Crypto.Cipher import AES

# === Settings ===
MAIN_KEY = base64.b64decode('WWcmdGMlREV1aDYlWmNeOA==')
MAIN_IV = base64.b64decode('Nm95WkRyMjJFM3ljaGpNJQ==')
RELEASEVERSION = "OB53"
USERAGENT = "Dalvik/2.1.0 (Linux; U; Android 13; CPH2095 Build/RKQ1.211119.001)"
SUPPORTED_REGIONS = {"IND", "BR", "US", "SAC", "NA", "SG", "RU", "ID", "TW", "VN", "TH", "ME", "PK", "CIS", "BD", "EUROPE"}

app = Flask(__name__)
CORS(app)
cache = TTLCache(maxsize=100, ttl=300)
cached_tokens = {}
uid_region_cache = {}

# === Encryption Helpers ===
def pad(text: bytes) -> bytes:
    padding_length = AES.block_size - (len(text) % AES.block_size)
    return text + bytes([padding_length] * padding_length)

def aes_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    aes = AES.new(key, AES.MODE_CBC, iv)
    return aes.encrypt(pad(plaintext))

async def json_to_proto(json_data: str, proto_message: Message) -> bytes:
    json_format.ParseDict(json.loads(json_data), proto_message)
    return proto_message.SerializeToString()

def decode_protobuf(encoded_data: bytes, message_type):
    instance = message_type()
    instance.ParseFromString(encoded_data)
    return instance

def get_account_credentials(region: str) -> str:
    r = region.upper()
    if r == "IND":
        return "uid=4363983977&password=ISHITA_0AFN5_BY_SPIDEERIO_GAMING_UY12H"
    elif r in {"BR", "US", "SAC", "NA"}:
        return "uid=4682784982&password=GHOST_TNVW1_RIZER_QTFT0"
    return "uid=4418979127&password=RIZER_K4CY1_RIZER_WNX02"

# === Core Logic ===
async def get_access_token(account: str, client: httpx.AsyncClient):
    url = "https://ffmconnect.live.gop.garenanow.com/oauth/guest/token/grant"
    payload = account + "&response_type=token&client_type=2&client_secret=2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3&client_id=100067"
    headers = {'User-Agent': USERAGENT, 'Content-Type': "application/x-www-form-urlencoded"}
    resp = await client.post(url, data=payload, headers=headers)
    data = resp.json()
    return data.get("access_token", "0"), data.get("open_id", "0")

async def create_jwt(region: str, client: httpx.AsyncClient):
    try:
        account = get_account_credentials(region)
        token_val, open_id = await get_access_token(account, client)
        body = json.dumps({"open_id": open_id, "open_id_type": "4", "login_token": token_val, "orign_platform_type": "4"})
        proto_bytes = await json_to_proto(body, FreeFire_pb2.LoginReq())
        payload = aes_cbc_encrypt(MAIN_KEY, MAIN_IV, proto_bytes)
        
        headers = {'User-Agent': USERAGENT, 'Content-Type': "application/octet-stream", 'ReleaseVersion': RELEASEVERSION}
        resp = await client.post("https://loginbp.ggblueshark.com/MajorLogin", data=payload, headers=headers)
        msg = decode_protobuf(resp.content, FreeFire_pb2.LoginRes)
        
        cached_tokens[region] = {
            'token': f"Bearer {msg.token}",
            'server_url': msg.server_url,
            'expires_at': time.time() + 25000
        }
        return cached_tokens[region]
    except Exception as e:
        print(f"Failed to create JWT for {region}: {e}")
        return None

async def get_token_info(region: str, client: httpx.AsyncClient):
    info = cached_tokens.get(region)
    if info and time.time() < info['expires_at']:
        return info['token'], info['server_url']
    new_info = await create_jwt(region, client)
    if new_info:
        return new_info['token'], new_info['server_url']
    return None, None

async def fetch_player_data(uid, region, client: httpx.AsyncClient):
    token, server = await get_token_info(region, client)
    if not token or not server:
        return None
    
    payload = await json_to_proto(json.dumps({'a': int(uid), 'b': 7}), main_pb2.GetPlayerPersonalShow())
    data_enc = aes_cbc_encrypt(MAIN_KEY, MAIN_IV, payload)
    
    headers = {
        'User-Agent': USERAGENT, 'Content-Type': "application/octet-stream",
        'Authorization': token, 'ReleaseVersion': RELEASEVERSION
    }
    
    resp = await client.post(f"{server}/GetPlayerPersonalShow", data=data_enc, headers=headers, timeout=5.0)
    if resp.status_code == 200:
        return json.loads(json_format.MessageToJson(decode_protobuf(resp.content, AccountPersonalShow_pb2.AccountPersonalShowInfo)))
    return None

# === Routes ===
@app.route('/player-info')
def get_account_info():
    uid = request.args.get('uid')
    if not uid:
        return jsonify({"error": "Please provide UID."}), 400

    # Cache check
    cache_key = f"info_{uid}"
    if cache_key in cache:
        return jsonify(cache[cache_key])

    async def run_search():
        async with httpx.AsyncClient() as client:
            # 1. Try cached region first
            if uid in uid_region_cache:
                res = await fetch_player_data(uid, uid_region_cache[uid], client)
                if res: return res
            
            # 2. Search all regions in parallel (Much faster than a loop)
            tasks = [fetch_player_data(uid, r, client) for r in SUPPORTED_REGIONS]
            results = await asyncio.gather(*tasks)
            
            for idx, res in enumerate(results):
                if res and "basicInfo" in res:
                    region_list = list(SUPPORTED_REGIONS)
                    uid_region_cache[uid] = region_list[idx]
                    return res
            return None

    result = asyncio.run(run_search())
    if result:
        cache[cache_key] = result
        return jsonify(result)
    
    return jsonify({"error": "UID not found in any region."}), 404

@app.route('/')
def health():
    return jsonify({"status": "running"}), 200

# Vercel needs the 'app' object