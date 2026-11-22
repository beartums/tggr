// Groups: the editor in Settings → Groups (each group's name and optional
// attributes -- a layout, Quality checks it ignores) and "Group…" for a
// selection (a track can be in several). Backend: core/groups.py.
import { api, Group } from './api'
import { toast } from './toast'
import { esc } from './util'
import { errText } from './staged'
import { QUALITY_ISSUES } from './quality'

const LAYOUT_HINT = '{album_artist_the}/{album_the}/{track_number:02d} {title}'

function layoutAttr(layout: string): string {
  return `<div class="group-attr" data-attr="layout">
    <div class="group-attr-h">Layout <span class="ma-dim">overrides the library and default layouts for these tracks</span>
      <button type="button" class="btn btn-ghost btn-sm btn-icon" data-attr-remove title="Remove: the library's or default layout applies">✕</button></div>
    <input type="text" spellcheck="false" data-layout value="${esc(layout)}" placeholder="${esc(LAYOUT_HINT)}" />
  </div>`
}

// Every Quality check applies to a group unless it's listed here: only the
// ignored ones are shown, and "+ Ignore a Quality check" adds one.
const label = (key: string) => QUALITY_ISSUES.find(q => q.key === key)
function ignoredChip(key: string): string {
  const q = label(key)
  return `<span class="ignored-check" data-quality="${esc(key)}" title="${esc(q?.group ? q.group + ': ' : '')}${esc(q?.label ?? key)}: these tracks are not listed or counted in it">${esc(q?.label ?? key)}<button type="button" class="ignored-check-x" data-unignore aria-label="Apply this check again" title="Apply this check again">✕</button></span>`
}
function qualityAttr(off: string[]): string {
  return `<div class="group-attr" data-attr="quality">
    <div class="group-attr-h">Ignored Quality checks <span class="ma-dim">these tracks are left out of them (not listed, not counted); every other check applies</span></div>
    <div class="tog-row">${off.map(ignoredChip).join('')}</div>
  </div>`
}
function ignorePicker(off: string[]): string {
  const left = QUALITY_ISSUES.filter(q => !off.includes(q.key))
  return `<select class="ignore-pick" data-ignore-pick aria-label="Quality check to ignore">
    <option value="">Ignore which check?</option>
    ${left.map(q => `<option value="${esc(q.key)}">${esc(q.group ? q.group + ' · ' : '')}${esc(q.label)}</option>`).join('')}
  </select>`
}
const ignoreBtn = '<button type="button" class="btn btn-link btn-sm" data-attr-add="quality">+ Ignore a Quality check</button>'
const offOf = (c: HTMLElement) => [...c.querySelectorAll<HTMLElement>('[data-quality]')].map(b => b.dataset.quality!)

function card(g: Group & { tracks?: number }): string {
  const hasLayout = !!g.layout, hasQuality = g.quality_off.length > 0
  return `<div class="group-card" data-id="${esc(g.id)}">
    <div class="group-head">
      <input type="text" class="group-name" value="${esc(g.name)}" placeholder="Group name" aria-label="Group name" />
      <span class="ma-dim">${(g.tracks ?? 0).toLocaleString()} track${g.tracks === 1 ? '' : 's'}</span>
      <button type="button" class="btn btn-ghost btn-sm" data-group-remove title="Delete this group (its tracks just leave it; nothing changes in the files)">Delete</button>
    </div>
    ${hasLayout ? layoutAttr(g.layout) : ''}${hasQuality ? qualityAttr(g.quality_off) : ''}
    <div class="group-add-attr">${hasLayout ? '' : '<button type="button" class="btn btn-link btn-sm" data-attr-add="layout">+ Layout</button>'}${g.quality_off.length < QUALITY_ISSUES.length ? ignoreBtn : ''}</div>
  </div>`
}

export function renderGroups(el: HTMLElement, groups: (Group & { tracks?: number })[]) {
  el.innerHTML = groups.length ? groups.map(card).join('')
    : '<p class="settings-hint group-none">No groups yet. Add one, then put tracks in it with <strong>Files ▾ → Groups…</strong>.</p>'
}

export function wireGroups(el: HTMLElement, addBtn: HTMLElement) {
  el.addEventListener('change', (e) => {
    const pick = (e.target as HTMLElement).closest<HTMLSelectElement>('[data-ignore-pick]')
    if (!pick || !pick.value) return
    const c = pick.closest<HTMLElement>('.group-card')!
    let a = c.querySelector<HTMLElement>('[data-attr="quality"]')
    if (!a) { c.querySelector('.group-add-attr')!.insertAdjacentHTML('beforebegin', qualityAttr([])); a = c.querySelector<HTMLElement>('[data-attr="quality"]')! }
    a.querySelector('.tog-row')!.insertAdjacentHTML('beforeend', ignoredChip(pick.value))
    pick.insertAdjacentHTML('afterend', offOf(c).length < QUALITY_ISSUES.length ? ignoreBtn : '')
    pick.remove()
  })
  addBtn.addEventListener('click', () => {
    el.querySelector('.group-none')?.remove()
    el.insertAdjacentHTML('beforeend', card({ id: '', name: '', layout: '', quality_off: [] }))
    el.querySelector<HTMLInputElement>('.group-card:last-child .group-name')!.focus()
  })
  el.addEventListener('click', (e) => {
    const t = e.target as HTMLElement
    const c = t.closest<HTMLElement>('.group-card')
    if (!c) return
    const add = t.closest<HTMLElement>('[data-attr-add]')
    if (add?.dataset.attrAdd === 'layout') {
      c.querySelector('.group-add-attr')!.insertAdjacentHTML('beforebegin', layoutAttr(''))
      add.remove()
      c.querySelector<HTMLInputElement>('[data-layout]')?.focus()
    } else if (add) {
      // Which check to ignore: a list of the ones that still apply, in place of the button.
      add.insertAdjacentHTML('afterend', ignorePicker(offOf(c)))
      add.remove()
      c.querySelector<HTMLSelectElement>('[data-ignore-pick]')!.focus()
    } else if (t.closest('[data-attr-remove]')) {
      t.closest<HTMLElement>('.group-attr')!.remove()
      c.querySelector('.group-add-attr')!.insertAdjacentHTML('afterbegin',
        '<button type="button" class="btn btn-link btn-sm" data-attr-add="layout">+ Layout</button>')
    } else if (t.closest('[data-unignore]')) {
      t.closest('[data-quality]')!.remove()
      const a = c.querySelector<HTMLElement>('[data-attr="quality"]')!
      if (!a.querySelector('[data-quality]')) a.remove()
      if (!c.querySelector('[data-attr-add="quality"], [data-ignore-pick]')) c.querySelector('.group-add-attr')!.insertAdjacentHTML('beforeend', ignoreBtn)
    } else if (t.closest('[data-group-remove]')) {
      c.remove()
    }
  })
}

/** The groups as edited, or null when the list never loaded (leave them as they are). */
export function readGroups(el: HTMLElement): Group[] | null {
  if (!el.dataset.loaded) return null
  return [...el.querySelectorAll<HTMLElement>('.group-card')].map(c => ({
    id: c.dataset.id ?? '',
    name: c.querySelector<HTMLInputElement>('.group-name')!.value.trim(),
    layout: c.querySelector<HTMLInputElement>('[data-layout]')?.value.trim() ?? '',
    quality_off: offOf(c),
  }))
}

/**
 * "Group…": which groups the selection is in (all / some / none of it), and
 * putting it in or taking it out of each, or a new one.
 */
export async function openGroupMembership(ids: number[], onDone: () => Promise<void>) {
  const n = `${ids.length.toLocaleString()} track${ids.length !== 1 ? 's' : ''}`
  const overlay = document.createElement('div')
  overlay.className = 'modal-overlay'
  overlay.innerHTML = `<div class="modal-card group-modal">
      <div class="modal-title">Groups for ${n}</div>
      <ul class="group-members"></ul>
      <form class="group-new"><input type="text" placeholder="New group" aria-label="New group name" /><button class="btn btn-sm" type="submit">Create and add</button></form>
      <p class="settings-hint">A group's layout and Quality checks are set in Settings → Groups.</p>
      <div class="modal-actions"><button class="btn btn-primary" data-close>Done</button></div>
    </div>`
  document.body.appendChild(overlay)
  const list = overlay.querySelector<HTMLElement>('.group-members')!
  let changed = false
  const close = async () => { overlay.remove(); if (changed) await onDone() }
  overlay.addEventListener('click', e => { if (e.target === overlay) close() })
  overlay.querySelector('[data-close]')!.addEventListener('click', close)

  const render = async () => {
    let groups, of
    try { [groups, of] = await Promise.all([api.groups.list(), api.groups.of(ids)]) } catch (e) { toast(`Groups: ${errText(e)}`, 'error'); return }
    list.innerHTML = groups.length ? groups.map(g => {
      const k = of.in[g.id] ?? 0
      const state = k === 0 ? '' : k === of.total ? '✓' : '–'
      return `<li><span class="group-state" aria-hidden="true">${state}</span><span class="group-member-name">${esc(g.name)}</span>
        <span class="ma-dim">${k === 0 ? 'not in it' : k === of.total ? 'all in it' : `${k.toLocaleString()} of ${of.total.toLocaleString()} in it`}</span>
        ${k < of.total ? `<button class="btn btn-ghost btn-sm" data-add="${esc(g.id)}">Add</button>` : ''}
        ${k > 0 ? `<button class="btn btn-ghost btn-sm" data-remove="${esc(g.id)}">Remove</button>` : ''}</li>`
    }).join('') : '<li class="ma-dim">No groups yet: make one below.</li>'
  }
  list.addEventListener('click', async (e) => {
    const b = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-add], [data-remove]')
    if (!b) return
    b.disabled = true
    try {
      await api.groups.setMembers(ids, (b.dataset.add ?? b.dataset.remove)!, !!b.dataset.add)
      changed = true
      await render()
    } catch (err) { toast(`Groups: ${errText(err)}`, 'error'); b.disabled = false }
  })
  overlay.querySelector<HTMLFormElement>('.group-new')!.addEventListener('submit', async (e) => {
    e.preventDefault()
    const input = (e.currentTarget as HTMLFormElement).querySelector('input')!
    const name = input.value.trim()
    if (!name) return
    try {
      const s = await api.settings.get()
      const saved = await api.settings.update({ groups: [...s.groups, { id: '', name, layout: '', quality_off: [] }] })
      const g = saved.groups.find(x => x.name === name)!
      await api.groups.setMembers(ids, g.id, true)
      changed = true
      input.value = ''
      await render()
    } catch (err) { toast(`New group: ${errText(err)}`, 'error') }
  })
  await render()
}
