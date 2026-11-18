# Self-hosting

The image is `ghcr.io/populii/domus`, tagged by calendar version (`2026.11.0`, or `2026.11` for that month's newest) and `latest`. It serves on
port 8765 as uid 99, gid 100. Its one folder is `/site`, holding the site project, the built model and
saved sketches; back that up and you've backed up everything.

With nothing in `/site` it asks for an address and builds the model there. `/demo` shows a made-up site. Once `/site` has a
project the address box goes away, as that container holds one site.

## Many sites in one container

Set `DOMUS_SITES` to a folder, mount it, and the container holds as many sites as you like. `/` lists them and has the address box, each site lives at
`/s/<name>/` with its own model, scans and sketches, and one build runs at a time. Each site is a subfolder, so back up the whole folder. To retire a site,
move its folder out. To bring in an existing one, stop the container and move its project folder in, with a name of lowercase letters, digits and dashes.

Commands need the site named: `docker exec domus domus --site /sites/<name> build`.

## Settings

| Variable | Default | What it does |
|---|---|---|
| `DOMUS_PORT` | `8765` | Port to serve on |
| `DOMUS_TRUST_PROXY` | `0` | `1` takes the user's name from `X-Forwarded-User`, for labelling sketches and recording who accepted, rejected or undid a change. Set it only behind a login proxy that sets that header itself |
| `DOMUS_LINZ_KEY` | unset | Free LINZ Data Service key, for real boundaries and buildings in New Zealand. Without it, and for addresses elsewhere, address lookups go to OpenStreetMap |
| `DOMUS_SITES` | unset | A folder of sites: one container serves them all, as below |
| `DOMUS_ALLOWED_HOSTS` | unset | Comma-separated host names the server answers to, such as `domus.example.com`. IP addresses and localhost always work; any other name gets a 403 |
| `DOMUS_MAX_SITES` | `100` | How many sites the address box can make on a many-site server |
| `DOMUS_ACCESS_LOG` | unset | Any value logs each request |

## Docker Compose

```sh
docker compose -f deploy/compose.yml up -d
```

`DOMUS_SITE_DIR` picks the folder and `DOMUS_VERSION` the version. The folder must be writable by
uid 99.

## Unraid

Copy `deploy/unraid/domus.xml` to `/boot/config/plugins/dockerMan/templates-user/my-domus.xml`,
then add the container from that template under **Docker > Add Container**. Pick your network there; on a
macvlan network the container gets its own IP and the port setting does nothing. Saved sketches are SQLite, so give the Site
folder a direct pool path such as `/mnt/cache/appdata/domus/site`, not `/mnt/user`, and make it owned by 99:100.

## Build a site in the container

Or skip the page and use the command line, with an address or your own boundary:

```sh
docker exec -it domus domus create "12 Example Street, Christchurch"
docker exec -it domus domus new /site --name "My place" --centre -43.53,172.63
docker exec -it domus domus fetch
docker exec -it domus domus build
```

Copy a real boundary into `/site` first and use `--boundary /site/lot.geojson` if you have one. Reload the
page after a build. `create`, `fetch`, `marks`, `title` and `points` are the only commands that go out to the
internet: to LINZ's data service and its public AWS buckets, OpenStreetMap without a key, the Copernicus ground
model's open copy on AWS outside New Zealand, and OpenTopography for a point cloud. To edit a site's files, use the
folder on the host.

Video reconstruction runs in the container too, on the CPU, and a long clip takes a good while: see [video.md](video.md).

## A login in front

The app has no login. Anyone who can reach the port can see the model, change sketches, add sites, upload
videos and start builds, so on a
network you don't fully trust, put a login proxy in front and let only the proxy reach the container.
Watch for this on macvlan: the container's own IP is reachable from the LAN without going through the
proxy, so limit that at your firewall if it matters to you.

Add the proxy's host name to `DOMUS_ALLOWED_HOSTS`. With Traefik and oauth2-proxy:

```yaml
http:
  routers:
    domus:
      rule: Host(`site.example.org`)
      entryPoints: [https]
      middlewares: [oauth2-proxy-auth]    # your forward-auth middleware
      service: domus
      tls: {}
  services:
    domus:
      loadBalancer:
        servers:
          - url: http://domus:8765
```

Have the forward-auth middleware pass `X-Forwarded-User` through and set `DOMUS_TRUST_PROXY=1` to label
sketches with who drew them.

Health checks can use `/healthz`. `/api/version` reports the version, and when the model was built and by which Domus.

## Upgrading

Pull the new tag and recreate the container. `/site` is untouched. A model an older Domus built is built again the
first time its site is opened, one build at a time, and the old model shows until the new one is ready. To have it
ready sooner, run `domus build` in the container after upgrading. If the build fails, the old model stays and the
site's `.domus/build.log` says why.
