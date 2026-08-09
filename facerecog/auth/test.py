from werkzeug.security import generate_password_hash, check_password_hash
import getpass

def main():
    password = getpass.getpass("Ilagay ang bagong password: ")
    confirm = getpass.getpass("I-confirm ulit ang password: ")

    if password != confirm:
        print("\n[ERROR] Hindi tugma ang dalawang password. Ulitin.")
        return

    hashed = generate_password_hash(password, method="pbkdf2:sha256")

    print("\n--- RESULT ---")
    print(f"Hash: {hashed}")
    print(f"Length: {len(hashed)}")

    is_valid = check_password_hash(hashed, password)
    print(f"Self-check (dapat True): {is_valid}")

    print("\nI-copy ang Hash sa itaas at ilagay sa database mo (column: password_hash).")

if __name__ == "__main__":
    main()