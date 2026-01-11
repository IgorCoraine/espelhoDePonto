from werkzeug.security import generate_password_hash
from dotenv import load_dotenv
import os
load_dotenv()
print(generate_password_hash(os.getenv("MINHA_SENHA_SECRETA"))) 