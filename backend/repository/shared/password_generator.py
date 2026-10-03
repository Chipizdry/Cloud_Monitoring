import string
import secrets
from backend.schemas import PasswordGeneratorSettings


def generate_password(settings: PasswordGeneratorSettings) -> str:
    characters = ""
    if settings.include_uppercase:
        characters += string.ascii_uppercase
    if settings.include_lowercase:
        characters += string.ascii_lowercase
    if settings.include_digits:
        characters += string.digits
    if settings.include_special:
        characters += string.punctuation

    if not characters:
        raise ValueError("No characters available for password generation.")

    return "".join(secrets.choice(characters) for _ in range(settings.length))
