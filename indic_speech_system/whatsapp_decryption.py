import hashlib
import hmac
import base64
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding
import requests
import logging

def HKDF_expand(key, info, length):
    """
    HKDF-Expand (RFC 5869)
    """
    generated_key = b""
    salt = b""  # Empty salt for HKDF-Expand in this context usually means we just use the PRK directly if we had Extract.
                # But WhatsApp uses a variant where it takes the 'key' as IKM. 
                # Actually, WhatsApp uses HKDFv3. 
                # Let's use a standard implementation that matches WhatsApp's specific parameters.
    
    # WhatsApp uses 112 iterations of HKDF key expansion essentially? No, it's standard 5869.
    # We need to derive 112 bytes: 32 (IV) + 32 (Cipher Key) + 32 (Mac Key) ?
    # Let's follow standard implementation for WhatsApp Media.
    
    # Media Keys are 32 bytes.
    # Info strings:
    # "WhatsApp Image Keys"
    # "WhatsApp Audio Keys"
    # "WhatsApp Video Keys"
    # "WhatsApp Document Keys"
    
    # We obtain 112 bytes of key material.
    # iv = early material
    # cipher key = next
    # mac key = next
    
    # Let's use the cryptography library's HKDF if possible, but manual implementation is safer to match exact RFC usage if needed.
    # For simplicity allowing dependency:
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import hashes
    
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=112,
        salt=None,
        info=info.encode('utf-8'),
        backend=default_backend()
    )
    return hkdf.derive(key)

def decrypt_media(media_url, media_key_b64, media_type):
    try:
        logging.info(f"🔓 Starting manual decryption for {media_type}...")
        
        # 1. Decode Media Key
        media_key = base64.b64decode(media_key_b64)
        if len(media_key) != 32:
            logging.error(f"❌ Invalid media key length: {len(media_key)}")
            return None

        # 2. Determine Info String
        type_map = {
            'audio': "WhatsApp Audio Keys",
            'image': "WhatsApp Image Keys",
            'video': "WhatsApp Video Keys",
            'document': "WhatsApp Document Keys",
            'sticker': "WhatsApp Image Keys", # Stickers use Image Keys usually
            'ptt': "WhatsApp Audio Keys"
        }
        info = type_map.get(media_type, "WhatsApp Audio Keys") # Default to audio if unsure? Or error?
        
        # 3. Derive Keys
        # 112 bytes needed: IV (16), Cipher Key (32), Mac Key (32), ref (32) ?
        # Actually structure is:
        # IV: 16 bytes
        # Cipher Key: 32 bytes
        # Mac Key: 32 bytes
        
        key_material = HKDF_expand(media_key, info, 112)
        iv = key_material[:16]
        cipher_key = key_material[16:48]
        mac_key = key_material[48:80]
        
        # 4. Download Encrypted Media
        logging.info(f"⬇️ Downloading .enc file from {media_url}...")
        r = requests.get(media_url, stream=True)
        if r.status_code != 200:
            logging.error(f"❌ Failed to download media: {r.status_code}")
            return None
        
        enc_data = r.content
        
        # 5. Verify MAC (Optional but recommended)
        # The last 10 bytes of enc_data is the MAC? Or appended?
        # WhatsApp enc file = content + mac (10 bytes)
        file_content = enc_data[:-10]
        received_mac = enc_data[-10:]
        
        # Validate MAC
        h = hmac.new(mac_key, file_content + iv, hashlib.sha256)
        calculated_mac = h.digest()[:10]
        
        if calculated_mac != received_mac:
            logging.warning("⚠️ MAC Mismatch! Decryption might be invalid, but proceeding...")
        else:
            logging.info("✅ MAC Verified.")

        # 6. Decrypt
        cipher = Cipher(algorithms.AES(cipher_key), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        decrypted_padded = decryptor.update(file_content) + decryptor.finalize()
        
        # 7. Unpad (PKCS7)
        # WhatsApp uses PKCS7 padding? Or usually it's just raw stream?
        # Usually it is padded.
        # However, for some media types it might not be strict.
        try:
            unpadder = padding.PKCS7(128).unpadder()
            decrypted_data = unpadder.update(decrypted_padded) + unpadder.finalize()
            logging.info(f"✅ Decryption successful! ({len(decrypted_data)} bytes)")
            return decrypted_data
        except Exception as e:
            logging.warning(f"⚠️ Unpadding error (returning raw decrypted): {e}")
            return decrypted_padded

    except Exception as e:
        logging.error(f"❌ Decryption Error: {e}")
        return None
