# Third-party notices

The application uses Python's standard library and original HTML/CSS/JavaScript. It does not vendor the supported applications' code, credentials, databases or plugins. Vendor names identify compatibility targets; no endorsement is implied.

Packaged executables contain CPython and a PyInstaller bootloader:

- **Python**: Python Software Foundation license and bundled component notices. The build includes the installed Python `LICENSE.txt` as `licenses/Python-LICENSE.txt`. [Python license](https://docs.python.org/3/license.html).
- **PyInstaller bootloader**: its licensing terms include an exception for distributing bundled applications. The build includes `COPYING.txt` as `licenses/PyInstaller-COPYING.txt`. [PyInstaller license](https://pyinstaller.org/en/stable/license.html).

SQLite and other runtime notices provided with Python remain applicable. Windows PowerShell/.NET and macOS AppleScript, Finder and process utilities are used from the host, not distributed in the package. Build tools and GitHub Actions are not part of the application's source license.

Retain the `licenses/` directory when redistributing packages. The project license does not replace third-party notices.
