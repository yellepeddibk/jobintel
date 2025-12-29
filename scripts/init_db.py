from jobintel.db import init_db


def main() -> None:
    init_db()
    print("Initialized database schema.")


if __name__ == "__main__":
    main()
