# The sublevels client UI: plan

Status: draft for Joe's review (2026-11-26). This plans the Ariadne client in `client/` (roadmap M3
and M4) as a set of components. It does not change the roadmap's decisions.

## 1. What it is

The sublevels client is the Direct Connect part of the Ariadne desktop and terminal apps:

- **P2P file sharing:** search, downloads, uploads, the queue, finished transfers, and share
  folders with hashing;
- **file lists:** download and browse another user's list, and queue files or folders from it;
- **chat:** a hub's main chat, the user list, and private messages;
- **admin:** for operators, the NMDC hub's server-side settings and tools (§4.9).

It runs beside two other component sets in one app shell:

- **nicotina's social components** (cy-pca/nicotina: the feed, posts, profiles, TX.WTF DMs);
- **cyprty's conference components** (cy-pca/cyprty: rooms, audio and video, avatars).

A user sees one app, signed in once with TX.WTF. A hub user who has a TX.WTF account links to their
nicotina profile, and a hub's main chat can be bridged to a conference room (#17).

The rules nicotina follows apply here too:

- **documents only:** no custom host and no compiled-in widget. Every widget or host feature the
  client needs is a generic libcvc feature (§5, and roadmap X1-X9);
- **the terminal is first-class:** every view works in `ari` with FTXUI and tvision, as well as in
  `arigl` and the browser, and a feature is done only when an `ari snapshot` test covers it.

## 2. Layout reference: eiskaltdcpp-qt6, through the clean room

Joe asked to base the layout on eiskaltdcpp's Qt 6 client, which is in Joe's fork and now packaged
on cvcpkg (transfix/eiskaltdcpp#13). Two facts shape how:

- eiskaltdcpp is GPL-3.0, and `client/` is MIT and clean-room (CLEANROOM.md, roadmap D5);
- the client is the half that clean-rooms eiskaltdcpp (D5).

So the layout comes in through the same door as the protocol:

1. **The spec writer** runs eiskaltdcpp-qt6 as a black box and writes `spec/client/LAYOUT.md` in
   their own words. It lists the windows and panes, what each shows (the columns, and what a row
   holds), the actions (menus, context menus, double clicks, shortcuts), and how the views connect.
   Screenshots stay in the private notes archive (transfix/nmdc-dev). The spec cites them; it
   doesn't contain them.
2. **The implementers** build original Ariadne documents from that spec and from §4. They do not
   open eiskaltdcpp's source, its `.ui` files or its screenshots.
3. **cyconv** (cy-pca/cyconv, LGPL-2.1; still at the planning stage) helps in two ways:
   - Its **Qt-to-Ariadne widget mapping and its list of widgets libcvc lacks** (its PLAN.md) are
     the starting point for §5. The table, the tree, the status bar, the toolbar, docks, modals and
     the file dialog are missing for both projects, so they are shared libcvc work.
   - Its converter (`ui2ari`, or the live `QWidget` probe) may be run on eiskaltdcpp only on the
     spec side, to give the spec writer a structural outline to describe. Converted output is never
     committed to cy-pca/sublevels. This needs cyconv's M1 (the IR and `ui2ari`). The client doesn't
     wait for it: step 1 works from the running program.

An alternative reference, if a second one helps: AirDC++'s Web UI, whose layout already fits a
browser and a narrow window. Its licence must be checked before anyone reads its source; until then,
it too is used only as a black box.

## 3. One state model, two backends

Every view binds to one local client state tree, `sublevels.client.*`, whatever the hub is:

| Path | Holds |
|---|---|
| `hubs.<h>.{name, topic, address, tls, keyprint, state, motd, rules}` | each hub connection |
| `hubs.<h>.users` (rows) | nick, share, description, client tag, mode, TLS, country, op, bot, account |
| `hubs.<h>.chat` | the main chat, paged as libcvc's ARIADNE_CHAT pages a room's |
| `pms.<conversation>` | private messages, with their source (§4.3) |
| `search.<s>.{query, results}` | searches and their results, grouped by TTH |
| `queue`, `transfers`, `finished` (rows) | the downloads and uploads |
| `filelists.<user>` | a browsed file list (a tree) |
| `share.{roots, hashing}` | the user's own share |
| `admin.<h>.*` | for operators: a hub's settings and logs (§4.9) |

Two adapters fill it:

- **Native, sublevels hubs (M3):** the hub's published `sublevels.hubs.<id>.*` subtree, its
  room-call functions, and relay-mode transfers (D6, X8/X9). Under libcvc this needs nothing new
  except X8/X9.
- **Vanilla NMDC hubs (M4):** the client side of the protocol over libcvc's net engine on the
  client (X1-X7: N4's client sockets, then N5's document verbs), from `spec/nmdc/` P1-P8.

The views never know which adapter a hub uses. That keeps the UI testable with a fake adapter that
writes the tree from fixtures, and it is how the `ari snapshot` tests run without a network.

## 4. Views

Each is a component in `client/` (`cvc://components/sublevels/<name>`), with a window as a `unit:`
and its logic as `functions:`, so nicotina's shell, or any app, can mount it.

1. **The shell entry.** A Direct Connect section in nicotina's window, with tabs or a side list:
   Hubs, Search, Transfers, Queue, Finished, File lists, Share, Public hubs, Admin (operators only)
   and Settings. A status bar shows the hubs connected, upload and download speed, and the share
   size and hashing progress.
2. **Hubs and a hub:**
   - the hub list: favorites, connected hubs, and connect by address, including `nmdcs://` with a
     keyprint;
   - a hub view: a header (name, topic, users, share, TLS and keyprint, MOTD and rules), the main
     chat (`text_view:` and `message_input:`), and the user list beside it in a resizable split;
   - the user list is a table (§5): nick, share, description, client, mode, TLS, country (D13), and
     the op and bot marks. Its context menu: private message, get file list, match queue, user
     info, the TX.WTF profile when the user has an account, and for operators the P6 actions
     (kick, redirect, ban, info);
   - a bridged hub shows the conference room's people and a "join the room" action into cyprty's
     components.
3. **Conversations.** One list of private conversations, whatever their source: NMDC private
   messages, TX.WTF DMs (nicotina's `dm` component, end-to-end encrypted) and conference DMs. Each
   carries a badge for its source. Replies go back over the same channel. Merging two sources for one
   person is a later option, and is never automatic (CU-D6).
4. **Search:** the query (terms, size, type, TTH), the hubs to search, and a results table grouped
   by TTH (file, size, users, slots, hub, TTH), with download, download to, and get file list.
5. **Transfers, Queue and Finished:** tables with progress and speed cells:
   - transfers: user, file, direction, progress, speed, remaining time, mode (active, passive or
     relay);
   - the queue: priority, sources, and folders as bundles;
   - finished: open, open folder, and remove.
6. **File lists:** a tree of folders beside a table of files, with search in the list, download a
   file or a folder, and match queue.
7. **Share:** roots (from host-held folder handles, X5), hashing progress (X6), refresh, and the
   share size.
8. **Public hubs:** loads the hub lists of P10 §4 (`hublist.xml.bz2`) from the configured lists,
   sublevels' own hublist mode among them, into a sortable and filterable table (name, address,
   users, share, country, min share and slots, reliability, TLS), with connect and favorite.
9. **Admin**, for operators and admins on a sublevels hub, over the admin API and room-call
   functions:
   - the hub's name, topic and description, and the MOTD and rules editors (#31);
   - limits, TLS and its keyprint, and the transport in use;
   - hub-list targets and their results (P9);
   - the location policy (D12);
   - bans, with add, edit and remove, and the operator log (P6);
   - the connection log with geolocation (D13);
   - analytics charts (D14, libcvc's `chart:`);
   - the users online, with kick, redirect and ban.

   Settings the server owns are shown read-only to operators and editable by admins.
10. **Settings,** the client's own: nick, description, email, connection mode (active, passive or
    relay, D6), ports (X2), TLS, download folder (X7), slots and speed limits, and notifications.

## 5. libcvc widgets the client needs

Generic Ariadne features, all in libcvc, each in ImGui and both terminal backends. Most match
cyconv's list of missing widgets, so one implementation serves both projects.

| # | Feature | Needed by |
|---|---|---|
| U1 | A data `table:` bound to state rows: columns with formats (text, size, speed, time, progress bar, icon or flag), sorting by column, resizable and hideable columns, single and multiple selection, a row context menu, a double-click action, and virtualized rows (100 000-row search results and file lists). A terminal draws it as aligned columns. | the user list, search, transfers, queue, finished, file lists, public hubs, admin |
| U2 | A `tree:` bound to nested state, with lazy expansion (a node's children loaded when it opens), selection and a context menu | file lists, share |
| U3 | A status bar: segments at the window's foot | the shell |
| U4 | A toolbar: a row of icon buttons with tooltips | the hub view, search |
| U5 | Modals and message boxes (confirm a ban, enter a reason) | admin, P6 actions |
| U6 | A folder picker that hands the document a host-held folder (beside `file-pick`), for share roots and the download folder | share, settings (X5, X7) |
| U7 | Notifications: the tray's icon and a system notification for a private message or a finished download | the shell |

Splits that resize already exist (§5.8's row-split panes), and so do `tabs:`, `list:`,
`text_view:`, `message_input:`, context menus, the menu bar, the tray and `chart:`.

## 6. Order

- **C0, spec and gaps.** The spec writer writes `spec/client/LAYOUT.md` from eiskaltdcpp-qt6, as a
  black box. U1-U7 are filed as libcvc work, coordinated with cyconv. The state model (§3) is
  written down in `spec/client/STATE.md`.
- **C1, the admin console** (§4.9) against today's hub. The server side is merged (#27, #29, #30,
  #31, #34), so operators get value first and it exercises U1 and U5.
- **C2, native hubs:** the hub list, the hub view, chat, the user list, conversations, and MOTD and
  rules. M3's core, over the native adapter.
- **C3, native search and relay transfers:** search, transfers, the queue and finished, with relay
  mode (D6; libcvc X8/X9).
- **C4, vanilla hubs** (M4): the vanilla adapter on N4/N5, plus file lists (U2), share (U6, X5/X6)
  and active-mode listeners.
- **C5, public hubs and polish:** the hub-list browser, notifications, and favorites.

Every step ships `ari snapshot` tests in FTXUI and tvision, plus `arigl`, with the fake adapter.

## 7. Decisions for Joe

| # | Question | Proposed |
|---|---|---|
| CU-D1 | The layout reference | eiskaltdcpp-qt6, through a black-box layout spec (§2). Nothing converted from its source enters sublevels. |
| CU-D2 | cyconv's part | its widget gap list drives U1-U7 as shared libcvc work. Its converter runs only on the spec side, when it exists. The client doesn't wait for it. |
| CU-D3 | Where the components live | `client/` in cy-pca/sublevels, as `cvc://components/sublevels/*`, shipped by a `sublevels-client` cvcpkg package that nicotina's recipe depends on |
| CU-D4 | Order | C1 (the admin console) first, then the native hub views |
| CU-D5 | Composition | mounted into nicotina's window through libcvc's app shell (the mounts work under way in libcvc, "SH1"); cyprty's components are mounted the same way |
| CU-D6 | Conversations | one list with a source badge on each conversation; no automatic merging of a person's NMDC and TX.WTF conversations |
