# Security Notice for MyKTV

This document explains the main security risks in the current build of the project and what users should know before running it on a machine with sensitive data or untrusted network access.

## Summary

The application is not clearly malicious, but it does perform several actions that increase security risk:

- it downloads and installs external executables and Python packages,
- it bypasses SSL certificate validation for some HTTPS downloads,
- it auto-updates core components like yt-dlp and AI libraries,
- it prepends local runtime folders to PATH and PYTHONPATH,
- it executes tools on user-selected media and remote URLs.

These features are useful for convenience, but they also widen the trust boundary.

## Risk areas

### 1. SSL verification is disabled

The app creates an HTTPS opener with an unverified SSL context in [MyKTV.py](MyKTV.py).

This means certificate validation is bypassed for some downloads. In practice, this can allow a malicious actor on the network to intercept or alter downloaded content without the app noticing.

Impact:
- fake or modified binaries could be downloaded,
- man-in-the-middle attacks become easier,
- users may install compromised software without warning.

### 2. The app downloads and executes third-party binaries

The code downloads Python runtime ZIPs, FFmpeg, Deno, and other packages from remote URLs, then extracts or installs them locally.

Examples include:
- Python embed ZIP download
- FFmpeg archive download
- Deno ZIP download
- pip installs for AI and media libraries

Impact:
- downloaded files are trusted automatically,
- there is no clear signature or checksum verification before extraction,
- a compromised upstream source could silently replace a required tool.

### 3. Automatic updates increase supply-chain risk

The program runs upgrade operations for tools such as yt-dlp and related dependencies.

This is convenient, but it means the app is effectively installing new code at runtime without a strict verification chain.

Impact:
- new versions may contain malware or regressions,
- package installation behavior can change over time,
- a malicious package index or compromised mirror could affect the tool.

### 4. Local folders are added to PATH and PYTHONPATH

The app modifies environment variables so that folders in the project directory are searched before system locations.

This is common for self-contained tools, but it also means that if an attacker places a malicious library or executable in those folders, it may be executed by the app.

Impact:
- malicious files in the app directory could be chosen over trusted files,
- local tampering becomes a risk if the project folder is writable by other users or malware.

### 5. External content is processed automatically

The app accepts user URLs and file paths and forwards them to yt-dlp, FFmpeg, and AI models.

This is normal for a media utility, but it introduces risk when the content is untrusted or malicious.

Impact:
- malformed media files could trigger vulnerabilities in codecs or parsers,
- downloaded data may be large or unexpected,
- remote content may have embedded exploits or crafted files.

## Overall assessment

The project appears to be a legitimate local utility rather than a malicious tool, but the current implementation should be treated as medium-risk for users who:

- run it on an untrusted network,
- run it with elevated privileges,
- keep the project directory writable by other users,
- do not verify downloaded archives before extraction.

## Recommended precautions

Before using the app, users should:

1. Run it only on a trusted Windows machine.
2. Keep the project folder in a user-writable, trusted location.
3. Avoid running it from a shared or network-mounted directory.
4. Prefer a clean environment with antivirus and firewall enabled.
5. Verify downloaded archives if possible before extraction.
6. Disable or review automatic update behavior if the app is used in a sensitive environment.
7. Prefer official or verified upstream sources for all dependencies.

## Best-practice improvement ideas

Recommended improvements include:

- remove TLS certificate bypassing,
- verify SHA256 or signatures for downloaded archives,
- pin dependency versions instead of allowing unrestricted auto-updates,
- isolate downloaded runtime files in a dedicated trusted directory,
- validate downloaded media before processing,
- warn users clearly when the app installs or updates software from the internet.

## Disclaimer

This security notice is intended as a practical risk summary for users and maintainers. It is not a guarantee of malicious behavior, but it highlights real security concerns in the current implementation.

If you are distributing this app to end users, publishing this notice alongside the executable or README is strongly recommended.
