# Download a release

Use this page when you download Agent Co-op by hand. [agent-setup.md](agent-setup.md) and [agent-update.md](agent-update.md) both send you here for the file list and the hash check.

## Which file

| Computer | File |
| --- | --- |
| Windows | `agentcoop-windows-x64.zip` (it contains `agentcoop.exe` and `agentcoopw.exe`) |
| macOS Apple silicon | `agentcoop-macos-arm64` |
| macOS Intel | `agentcoop-macos-x86_64` |
| Linux x64 | `agentcoop-linux-x64` |
| Linux arm64 | `agentcoop-linux-arm64` |
| Any, when Python 3.9 or newer is installed | `agentcoop.pyz` |

Releases before 0.2.2 have no Windows zip. Use `agentcoop-windows-x64.exe` for 0.2.0, and that file plus `agentcoopw-windows-x64.exe` for 0.2.1.

Every release also has `SHA256SUMS`. Download it from the same release. Do not use a sums file from a different version.

## Check the hash

Stop if the hash does not match, or if the sums file has no line for that file. Do not run the file.

Windows PowerShell:

```powershell
Get-FileHash -Algorithm SHA256 .\agentcoop-windows-x64.zip
```

macOS:

```bash
shasum -a 256 agentcoop-macos-arm64
```

Linux:

```bash
sha256sum agentcoop-linux-x64
```

Compare the hash with the line for that file name in `SHA256SUMS`.

## If the download fails

* No network: say so, and ask the person to download the file from the release page.
* GitHub says the API limit is reached: say so in one line, and give the release page URL so they can download the file by hand.
* The download is blocked: say so, and give the release page URL.
* You cannot run commands: ask the person to say the same sentence in Cursor.
