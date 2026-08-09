from werkzeug.security import generate_password_hash
import getpass

password = getpass.getpass("Enter admin password: ")
confirm = getpass.getpass("Confirm password: ")

if password != confirm:
    print("Passwords do not match. Try again.")
else:
    hashed = generate_password_hash(password)
    print("\nYour hashed password:")
    print(hashed)
    print("\nCopy this into your config/database as the admin password hash.")