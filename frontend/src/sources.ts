// Album sources (backend core/sources.py): Deezer and iTunes, for covers and,
// where the tag is empty, the label and year.
// - Settings → Album sources: on/off, what each may fill, the cover order, the
//   search-match policy (wired here; the markup is in template.ts).
// - The editor's source chips: one per source for the selected album folder,
//   an indicator with hover text (press and hold on a touchscreen).
// - Sources…: find the album on each source, pick / confirm / unlink, and fill
//   the label and year (staged, like every other edit).
import { api, AppSettings, CoverSourceKey, SourceCandidate, SourceField, SourceKey, SourceLink, SourceMatch, Track } from './api'
import { errText } from './staged'
import { toast } from './toast'
import { esc } from './util'

export const SOURCE_NAMES: Record<CoverSourceKey, string> = { caa: 'Cover Art Archive', itunes: 'iTunes', deezer: 'Deezer', discogs: 'Discogs' }
const SOURCES: SourceKey[] = ['deezer', 'itunes']
const METHOD: Record<string, string> = { barcode: 'by barcode', isrc: 'by ISRC', search: 'by search', manual: 'picked by you' }

// ─── Press and hold: a title as a bubble, for touchscreens with no hover ──────

let holdBubble: HTMLElement | null = null
/** Inside `root`, holding a finger on anything with a title shows that title until it lifts. */
export function holdTips(root: HTMLElement) {
  if (root.dataset.holdTips) return
  root.dataset.holdTips = '1'
  let timer = 0
  const hide = () => { clearTimeout(timer); holdBubble?.remove(); holdBubble = null }
  root.addEventListener('pointerdown', e => {
    if (e.pointerType !== 'touch') return
    const el = (e.target as HTMLElement).closest<HTMLElement>('[title]')
    if (!el || !root.contains(el)) return
    timer = window.setTimeout(() => {
      holdBubble = document.createElement('div')
      holdBubble.className = 'hold-tip'
      holdBubble.textContent = el.title
      document.body.appendChild(holdBubble)
      const r = el.getBoundingClientRect(), b = holdBubble.getBoundingClientRect()
      holdBubble.style.left = `${Math.max(8, Math.min(window.innerWidth - b.width - 8, r.left))}px`
      holdBubble.style.top = `${r.top - b.height - 6 > 8 ? r.top - b.height - 6 : r.bottom + 6}px`
    }, 450)
  })
  for (const ev of ['pointerup', 'pointercancel', 'pointerleave', 'scroll']) root.addEventListener(ev, hide, true)
  // The long press would otherwise open the browser's own menu, or click.
  root.addEventListener('contextmenu', e => { if (holdBubble) e.preventDefault() })
  root.addEventListener('click', e => { if (holdBubble) { e.preventDefault(); e.stopPropagation(); hide() } }, true)
}

// ─── Settings → Album sources ────────────────────────────────────────────────

const pressed = (b: Element | null) => b?.getAttribute('aria-pressed') === 'true'
const press = (b: Element | null, on: boolean) => b?.setAttribute('aria-pressed', String(on))

function paintOrder(pane: HTMLElement, order: CoverSourceKey[]) {
  const list = pane.querySelector<HTMLElement>('#cover-order')!
  list.innerHTML = order.map((k, i) => `<li data-cover-src="${k}"><span>${esc(SOURCE_NAMES[k])}</span>
    <button type="button" class="btn btn-ghost btn-icon btn-sm" data-cover-move="-1" ${i ? '' : 'disabled'} title="Look here earlier" aria-label="Move ${esc(SOURCE_NAMES[k])} up">↑</button>
    <button type="button" class="btn btn-ghost btn-icon btn-sm" data-cover-move="1" ${i < order.length - 1 ? '' : 'disabled'} title="Look here later" aria-label="Move ${esc(SOURCE_NAMES[k])} down">↓</button></li>`).join('')
  pane.querySelector<HTMLInputElement>('#cover-order-value')!.value = order.join(',')
}
const readOrder = (pane: HTMLElement) =>
  pane.querySelector<HTMLInputElement>('#cover-order-value')!.value.split(',').filter(Boolean) as CoverSourceKey[]

function paintOn(pane: HTMLElement) {
  for (const src of SOURCES) {
    const on = pressed(pane.querySelector(`[data-src-on="${src}"]`))
    pane.querySelector(`[data-src-fields="${src}"]`)?.classList.toggle('vs-disabled', !on)
  }
  const auto = pressed(pane.querySelector('[data-src-policy="auto"]'))
  pane.querySelector<HTMLInputElement>('#src-threshold')!.disabled = !auto
}

export function loadSourceSettings(pane: HTMLElement, s: AppSettings) {
  for (const src of SOURCES) {
    press(pane.querySelector(`[data-src-on="${src}"]`), s[`source_${src}`] ?? true)
    for (const b of pane.querySelectorAll<HTMLElement>(`[data-src-field^="${src}:"]`)) {
      press(b, (s.source_fields?.[src] ?? []).includes(b.dataset.srcField!.split(':')[1] as SourceField))
    }
  }
  paintOrder(pane, s.cover_order?.length ? s.cover_order : ['caa', 'itunes', 'deezer', 'discogs'])
  for (const b of pane.querySelectorAll<HTMLElement>('[data-src-policy]')) press(b, b.dataset.srcPolicy === (s.source_search_policy ?? 'ask'))
  pane.querySelector<HTMLInputElement>('#src-threshold')!.value = String(s.source_auto_threshold ?? 0.9)
  paintOn(pane)
}

export function collectSourceSettings(pane: HTMLElement): Partial<AppSettings> {
  const fields: Partial<Record<SourceKey, SourceField[]>> = {}
  for (const src of SOURCES) {
    fields[src] = [...pane.querySelectorAll<HTMLElement>(`[data-src-field^="${src}:"][aria-pressed="true"]`)]
      .map(b => b.dataset.srcField!.split(':')[1] as SourceField)
  }
  const t = Number(pane.querySelector<HTMLInputElement>('#src-threshold')!.value)
  return {
    source_deezer: pressed(pane.querySelector('[data-src-on="deezer"]')),
    source_itunes: pressed(pane.querySelector('[data-src-on="itunes"]')),
    source_fields: fields,
    cover_order: readOrder(pane),
    source_search_policy: pressed(pane.querySelector('[data-src-policy="auto"]')) ? 'auto' : 'ask',
    source_auto_threshold: Number.isFinite(t) ? Math.min(1, Math.max(0.5, t)) : 0.9,
  }
}

export function wireSourceSettings(pane: HTMLElement) {
  pane.addEventListener('click', e => {
    const t = e.target as HTMLElement
    const on = t.closest<HTMLElement>('[data-src-on], [data-src-field]')
    if (on) { press(on, !pressed(on)); paintOn(pane); return }
    const pol = t.closest<HTMLElement>('[data-src-policy]')
    if (pol) {
      for (const b of pane.querySelectorAll('[data-src-policy]')) press(b, b === pol)
      paintOn(pane); return
    }
    const mv = t.closest<HTMLElement>('[data-cover-move]')
    if (mv) {
      const order = readOrder(pane), k = mv.closest<HTMLElement>('[data-cover-src]')!.dataset.coverSrc as CoverSourceKey
      const i = order.indexOf(k), j = i + Number(mv.dataset.coverMove)
      if (j < 0 || j >= order.length) return
      ;[order[i], order[j]] = [order[j], order[i]]
      paintOrder(pane, order)
    }
  })
}

// ─── Indicators ──────────────────────────────────────────────────────────────

type Mark = 'confirmed' | 'waiting' | 'missing'
const mark = (l: SourceLink): Mark => l.status === 'missing' ? 'missing' : l.confirmed ? 'confirmed' : 'waiting'

function linkTitle(l: SourceLink): string {
  const name = SOURCE_NAMES[l.source]
  if (l.status === 'missing') return `${name}: looked up ${new Date(l.at * 1000).toLocaleDateString()}, nothing right found. Click to look again or pick one`
  const what = [l.title, l.artist].filter(Boolean).join(' · ')
  const facts = [l.tracks ? `${l.tracks} tracks` : '', l.date ?? '', l.label ? `label ${l.label}` : '', `id ${l.album_id}`].filter(Boolean).join(' · ')
  const how = `${METHOD[l.method ?? ''] ?? ''}${l.score != null ? `, score ${l.score.toFixed(2)}` : ''}${l.reasons ? ` (${l.reasons})` : ''}`
  return `${name}: ${what}\n${facts}\nMatched ${how}\n${l.confirmed ? 'Confirmed' : 'Waiting for you to confirm it'} · click for more`
}

// ─── The editor's chips ──────────────────────────────────────────────────────

let chipsFor = ''
/** One chip per source for the selection's album folder (one folder only), and Sources…. */
export async function renderSourceChips(el: HTMLElement, tracks: Track[], onChange: () => Promise<void>) {
  holdTips(el)
  const dirs = new Set(tracks.map(t => t.directory))
  // Several folders, or a selection beyond this page (the editor's stand-in tracks have no folder).
  if (dirs.size !== 1 || !tracks[0]?.directory || tracks.some(t => !t.id)) { el.hidden = true; el.innerHTML = ''; chipsFor = ''; return }
  const directory = [...dirs][0], ids = tracks.map(t => t.id)
  chipsFor = directory
  let links: Partial<Record<SourceKey, SourceLink>>
  try { links = await api.sources.links(directory) } catch { el.hidden = true; return }
  if (chipsFor !== directory) return       // the selection moved on meanwhile
  el.hidden = false
  el.innerHTML = Object.values(links).map(l => `<button type="button" class="src-chip src-${mark(l!)}" data-src="${l!.source}" title="${esc(linkTitle(l!))}">
      <span class="src-dot" aria-hidden="true"></span>${esc(SOURCE_NAMES[l!.source])}</button>`).join('')
    + `<button type="button" class="btn btn-link btn-sm" data-src-open title="Find this album on Deezer and iTunes: covers, label, year">Sources…</button>`
  el.onclick = e => {
    const b = (e.target as HTMLElement).closest<HTMLElement>('[data-src], [data-src-open]')
    if (b) openSourcesDialog(ids, directory, onChange)
  }
}

// ─── Sources… ────────────────────────────────────────────────────────────────

export function openSourcesDialog(trackIds: number[], directory: string, onDone: () => Promise<void>) {
  const overlay = document.createElement('div')
  overlay.className = 'fix-confirm-overlay'
  overlay.innerHTML = `
    <div class="fix-confirm-modal src-modal" role="dialog" aria-label="Album sources">
      <div class="fix-confirm-header">
        <span class="fix-confirm-title">Album sources · <span class="ma-dim" title="${esc(directory)}">${esc(directory.split('/').pop() ?? directory)}</span></span>
        <button class="btn btn-ghost btn-icon fix-confirm-close" aria-label="Close">✕</button>
      </div>
      <div class="src-body" id="src-body"><p class="ma-dim">Looking…</p></div>
      <div class="settings-hint src-fill" id="src-fill"></div>
      <div class="fix-confirm-footer">
        <button class="btn btn-ghost" id="src-again" title="Look every source up again (what you confirmed is replaced only by what you pick)">Look again</button>
        <button class="btn btn-ghost" id="src-close">Close</button>
        <button class="btn btn-primary" id="src-fill-go" disabled>Fill</button>
      </div>
    </div>`
  document.body.appendChild(overlay)
  const $ = <T extends HTMLElement>(sel: string) => overlay.querySelector<T>(sel)!
  const body = $('#src-body'), fillEl = $('#src-fill'), fillBtn = $<HTMLButtonElement>('#src-fill-go')
  holdTips(overlay)
  let changed = false, m: SourceMatch | null = null
  const close = async () => { overlay.remove(); document.removeEventListener('keydown', onKey); if (changed) await onDone() }
  const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && document.body.lastElementChild === overlay) close() }
  document.addEventListener('keydown', onKey)
  overlay.addEventListener('click', e => { if (e.target === overlay) close() })
  $('.fix-confirm-close').addEventListener('click', close)
  $('#src-close').addEventListener('click', close)

  const cand = (src: SourceKey, c: SourceCandidate, linked: SourceLink | undefined) => {
    const on = linked?.status === 'linked' && linked.album_id === c.id
    return `<div class="src-cand${on ? ' src-cand-on' : ''}">
      ${c.cover ? `<img src="${esc(c.cover)}" alt="" loading="lazy" />` : '<span class="src-noimg">♪</span>'}
      <div class="src-cand-text"><div>${esc(c.title ?? '')} <span class="ma-dim">· ${esc(c.artist ?? '')}${c.date ? ` · ${esc(c.date.slice(0, 4))}` : ''}</span></div>
        <div class="ma-dim">${esc(c.reasons)} · score ${c.score.toFixed(2)}${c.label ? ` · ${esc(c.label)}` : ''}</div></div>
      ${on ? (linked!.confirmed ? '<span class="src-state" title="This album is linked and confirmed">✓</span>'
                                : `<button type="button" class="btn btn-ghost btn-sm" data-confirm="${src}" title="Yes, this is the album">Confirm</button>`)
           : `<button type="button" class="btn btn-ghost btn-sm" data-pick="${src}" data-id="${esc(c.id)}" title="Link this album instead, confirmed">Use this</button>`}
      ${c.link ? `<a class="btn btn-link btn-sm" href="${esc(c.link)}" target="_blank" rel="noopener" title="Open it on ${esc(SOURCE_NAMES[src])}">↗</a>` : ''}
    </div>`
  }
  function render() {
    if (!m) return
    body.innerHTML = SOURCES.map(src => {
      const l = m!.links[src], cands = m!.candidates[src] ?? []
      const err = m!.errors[src]
      // The link itself, when it isn't among this lookup's candidates (kept from before).
      const shown = l?.status === 'linked' && !cands.some(c => c.id === l.album_id)
        ? [{ source: src, id: l.album_id!, title: l.title ?? null, artist: l.artist ?? null, date: l.date ?? null, label: l.label ?? null,
             barcode: l.barcode ?? null, link: l.link ?? null, cover: l.cover ?? null, cover_size: l.cover_size ?? null,
             tracks: l.tracks ?? 0, score: l.score ?? 0, reasons: `${l.reasons ?? ''}${l.method ? ` · ${METHOD[l.method]}` : ''}` } as SourceCandidate, ...cands]
        : cands
      return `<section class="src-sec" data-sec="${src}">
        <div class="src-sec-h"><span class="src-dot src-${l ? mark(l) : 'missing'}" title="${esc(l ? linkTitle(l) : 'Not looked up')}"></span>
          <strong>${SOURCE_NAMES[src]}</strong>
          ${l?.status === 'linked' ? `<button type="button" class="btn btn-link btn-sm" data-unlink="${src}" title="Forget this link (nothing in your files changes)">Unlink</button>` : ''}
          <form class="src-paste" data-paste="${src}"><input type="text" placeholder="${src === 'deezer' ? 'Deezer album id or link' : 'iTunes album id or link'}" />
            <button class="btn btn-ghost btn-sm" type="submit" title="Link the album you name, confirmed">Link</button></form></div>
        ${err ? `<div class="review-error">${esc(err)}</div>` : ''}
        ${shown.length ? shown.map(c => cand(src, c, l)).join('')
                       : `<p class="ma-dim">${l?.status === 'missing' ? 'Nothing right found: no album whose tracks match these.' : 'Not looked up (switched off in Settings → Album sources?)'}</p>`}
      </section>`
    }).join('')
  }
  async function planFill() {
    fillBtn.disabled = true
    try {
      const f = await api.sources.fill(trackIds, true)
      const a = f.albums[0], v = a?.values ?? {}
      const parts = (['label', 'year'] as const).filter(k => v[k]).map(k => `${k} ${v[k]!.value} (${v[k]!.source})`)
      fillEl.textContent = !parts.length ? 'Nothing to fill: no confirmed source gives a label or year (Settings → Album sources).'
        : f.changes ? `Fill where empty: ${parts.join(', ')} → ${f.changes} of ${a.tracks} tracks (${a.fields.join(', ')}). Staged for review.`
        : `${parts.join(', ')}: every track already has these.`
      fillBtn.disabled = !f.changes
      fillBtn.textContent = f.changes ? `Fill ${f.changes} track${f.changes !== 1 ? 's' : ''}` : 'Fill'
    } catch (e) { fillEl.textContent = errText(e) }
  }
  async function look(refresh: boolean) {
    body.innerHTML = `<p class="ma-dim">Looking… (iTunes takes a few seconds per album)</p>`
    try { m = await api.sources.match(trackIds, refresh); render() } catch (e) { body.innerHTML = `<div class="review-error">${esc(errText(e))}</div>` }
    await planFill()
  }
  const relink = (src: SourceKey, l: SourceLink) => { if (m) { m.links[src] = l; changed = true; render(); planFill() } }
  body.addEventListener('click', async e => {
    const t = e.target as HTMLElement
    const conf = t.closest<HTMLElement>('[data-confirm]'), pick = t.closest<HTMLElement>('[data-pick]'), un = t.closest<HTMLElement>('[data-unlink]')
    try {
      if (conf) { const src = conf.dataset.confirm as SourceKey; relink(src, await api.sources.link(directory, src)) }
      else if (pick) {
        const src = pick.dataset.pick as SourceKey
        ;(pick as HTMLButtonElement).disabled = true
        relink(src, await api.sources.link(directory, src, pick.dataset.id!))
      } else if (un && m) {
        const src = un.dataset.unlink as SourceKey
        await api.sources.unlink(directory, src)
        delete m.links[src]; changed = true; render(); planFill()
      }
    } catch (err) { toast(`Album sources: ${errText(err)}`, 'error') }
  })
  body.addEventListener('submit', async e => {
    e.preventDefault()
    const form = e.target as HTMLFormElement, src = form.dataset.paste as SourceKey
    const raw = form.querySelector('input')!.value.trim()
    const id = raw.match(/(\d{4,})(?!.*\d{4,})/)?.[1]
    if (!id) { toast('Give the album id or its link', 'error'); return }
    try { relink(src, await api.sources.link(directory, src, id)); toast(`${SOURCE_NAMES[src]}: linked`, 'success') }
    catch (err) { toast(`Album sources: ${errText(err)}`, 'error') }
  })
  $('#src-again').addEventListener('click', () => look(true))
  fillBtn.addEventListener('click', async () => {
    fillBtn.disabled = true
    try {
      const f = await api.sources.fill(trackIds, false)
      changed = true
      toast(f.staged ? `Label / year staged on ${f.changes} track${f.changes !== 1 ? 's' : ''} — review them in Changes`
                     : `Label / year written to ${f.applied ?? f.changes} track${f.changes !== 1 ? 's' : ''}`, 'success')
      await planFill()
    } catch (e) { toast(`Fill failed: ${errText(e)}`, 'error'); fillBtn.disabled = false }
  })
  look(false)
}
