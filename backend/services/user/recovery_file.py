from io import BytesIO


async def generate_recovery_file(recovery_code: str):

    encrypted_code = recovery_code.encode()  # Код без шифрования
    # Создание бинарного файла с зашифрованным кодом
    encrypted_file = BytesIO(encrypted_code)
    encrypted_file.name = "recovery_key.bin"
    return encrypted_file
