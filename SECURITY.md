# Security

Report a security problem through GitHub's private reporting, on this repo's Security tab, and not in a public issue. Say what you found and how to repeat it.

Only the latest release gets fixes.

## What to expect from Domus

- The app has no login. Anyone who can reach its port can see every site on it, change sketches, add sites, upload videos and start builds. Keep it on your own machine, or put a login proxy in front: see [docs/self-hosting.md](docs/self-hosting.md).
- The server answers to IP addresses and localhost. Any other name has to be listed in `DOMUS_ALLOWED_HOSTS`.
- Treat a site folder from someone else as you would any other download. Domus reads its data without running code from it, but its build uses geospatial and video libraries on files you didn't make. A site's own code, its extensions, runs only once you've read it and trusted it on that machine with `domus site trust`, and the container image runs none.
- Typing an address sends it to LINZ, or to OpenStreetMap's public services without a LINZ key.
