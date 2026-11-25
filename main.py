"""Uruchomienie okna aplikacji."""

from gui import DataApp


def main() -> None:
    app = DataApp()
    app.mainloop()


if __name__ == "__main__":
    main()
