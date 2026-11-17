# Monospace website

The buildless product website for https://monospace.arp244.dev. Serve `website/` as the static root:

```sh
python3 -m http.server 4173 --directory website
```

Open http://localhost:4173. No package installation, compilation, or external JavaScript dependencies are needed. Reader/compose screenshots are copied from `docs/assets/`; both show the project's illustrative demo mailbox. The page supports screenshot switching, an interactive explanation of the three real send policies, clipboard installation with a text-selection fallback, and native FAQ disclosures. JavaScript is optional for reading the page and installing the client.

`index.html` owns content and metadata, `styles.css` owns the responsive visual design, and `script.js` owns the small optional interactions. `assets/favicon.svg` is the code-native wordmark; the product screenshots remain the project's original SVGs.

## Installer endpoint

`website/install.sh` serves the installer at `https://monospace.arp244.dev/install.sh` when this folder is deployed. The README and website use:

```sh
curl -fsSL https://monospace.arp244.dev/install.sh | sh
```

The root `install.sh` is the installer source. After changing it, refresh the published copy:

```sh
cp install.sh website/install.sh
```

CI checks that both copies match. Keep `install.sh` available as a static file when configuring the host; it must return the shell script rather than an HTML fallback. The installer downloads the application source from the GitHub repository.
