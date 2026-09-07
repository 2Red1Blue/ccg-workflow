# Background Web UI tabs in daily Chrome

This extension creates task tabs with `active:false` in an existing normal Chrome
window. It never focuses or creates a window. At task completion, after a three
second grace, it removes only the stored tab ID if its URL still exactly matches
the task page. Navigation away or an in-flight navigation prevents auto-close.
Closing a task tab manually does not cause it to reopen during that task.
Navigating away permanently releases automatic closing, even if you return.
Ownership is persisted across browser restarts; only one unambiguous previously
owned task URL can be recovered. Ambiguous or missing tabs are left alone.

Chrome's `tabs` permission exposes tab URL metadata so in-flight navigation away
can be detected reliably. The extension processes only its recorded task IDs and
loopback recovery queries; it never reads page bodies or transmits data remotely.

The native host polls private task leases once per second. A wrapper renews its
lease every two seconds and removes it on shutdown. A killed wrapper's lease
expires after ten seconds. Leases contain only random IDs and loopback URLs;
neither prompts nor credentials are passed to the extension.

Install the committed files with `python3 scripts/install-browser-companion.py`,
then enable Developer mode at `chrome://extensions` and Load unpacked from the
printed extensionPath. The stable public manifest key fixes the extension ID;
the native host accepts only that ID. No private signing key is shipped.

The native port keeps the extension worker alive while Chrome runs. A reconnect
alarm retries a failed native connection; its badge `!` signals disconnection
and `E` signals a storage/tab operation error that a later snapshot retries.
When no normal Chrome window exists, pages are deferred until one exists.

Controls: unset `CODEAGENT_WEB_UI_AUTO_OPEN` uses the companion, `false` disables
automatic tabs while retaining the URL, and `true` uses the legacy system-browser
opener (without reliable focus/auto-close guarantees). No `--lite` is enabled.
Without the extension, unset mode leaves the ordinary manually accessible Web UI.

Verification: `node --test browser-companion/tabs.test.js` and
`python3 -m unittest discover -s browser-companion -p 'test_*.py'`.
