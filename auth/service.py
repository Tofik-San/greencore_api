import secrets

def generate_code() -> str:
    return str(secrets.randbelow(900000) + 100000)  # 6 цифр

def generate_session_token() -> str:
    return secrets.token_hex(32)
