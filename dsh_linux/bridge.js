// DeepSeek Harness Linux shell bridge, injected at document-start by the
// native wrapper. Exposes the `dshDesktop` carrier consumed by the settings
// UI (updates + appearance) and relays native pushes through __DSH_BRIDGE__.
(() => {
  'use strict'
  if (globalThis.__DSH_BRIDGE_INSTALLED__) return
  globalThis.__DSH_BRIDGE_INSTALLED__ = true

  const updateListeners = []
  const appearanceListeners = []
  let updatesState = null
  let appearanceState = null
  const pending = new Map()

  // -- DOM application of the appearance state --------------------------------

  const RADIUS_BASES = {
    '--dsw-radius-xs': 4,
    '--dsw-radius-sm': 8,
    '--dsw-radius-md': 12,
    '--dsw-radius-lg': 16,
    '--dsw-radius-xl': 20,
    '--dsw-radius-panel': 28,
  }
  let userStyle = null

  function keepStyleLast() {
    // The app appends its own stylesheets after ours; re-appending keeps the
    // user stylesheet last so equal-specificity rules win by document order.
    if (document.head.lastElementChild !== userStyle) {
      document.head.appendChild(userStyle)
    }
  }

  function ensureUserStyle() {
    if (userStyle !== null) return
    userStyle = document.createElement('style')
    userStyle.id = 'dsh-linux-user-css'
    document.head.appendChild(userStyle)
    new MutationObserver(keepStyleLast).observe(document.head, { childList: true })
  }

  function applyAppearance(state) {
    if (!state || typeof document === 'undefined' || document.documentElement === null) return
    const scale = Math.min(2, Math.max(0, Number(state.cornerRadiusScale) || 1))
    for (const [name, base] of Object.entries(RADIUS_BASES)) {
      // Inline custom properties beat any stylesheet rule, author or user.
      document.documentElement.style.setProperty(name, `${Math.round(base * scale * 10) / 10}px`)
    }
    ensureUserStyle()
    userStyle.textContent = typeof state.customCss === 'string' ? state.customCss : ''
  }

  function post(payload) {
    try {
      window.webkit.messageHandlers.dshlinux.postMessage(JSON.stringify(payload))
    } catch (error) {
      // Non-native browser: the fallback store inside the page owns state.
    }
  }

  function request(method, args) {
    const id = 'r' + Math.random().toString(36).slice(2) + Date.now().toString(36)
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject })
      post({ type: 'req', id, method, args: args || [] })
      setTimeout(() => {
        if (pending.has(id)) {
          pending.delete(id)
          reject(new Error('dsh-linux bridge timeout: ' + method))
        }
      }, 30000)
    })
  }

  // -- external links ----------------------------------------------------------
  // The page is a single-page app: account, top-up, and other outbound links
  // are often handled in JavaScript, so no WebKit navigation ever happens.
  // Intercept external anchor clicks at the capture phase (our listener runs
  // first, being installed at document-start) and hand them to the native
  // shell, which opens the system default browser.

  function externalClick(event) {
    if (event.defaultPrevented || event.button !== 0) return
    const target = event.target
    const anchor = target && typeof target.closest === 'function'
      ? target.closest('a[href]')
      : null
    if (!anchor) return
    let href = null
    try {
      href = new URL(anchor.getAttribute('href'), location.href).href
    } catch (_error) {
      return
    }
    if (!href.startsWith('http://') && !href.startsWith('https://')) return
    if (new URL(href).origin === location.origin) return
    event.preventDefault()
    event.stopPropagation()
    post({ type: 'open', uri: href })
  }

  document.addEventListener('click', externalClick, true)

  const updates = {
    status: () => updatesState !== null
      ? Promise.resolve(updatesState)
      : request('updates.status', []).then(state => { updatesState = state; return state }),
    subscribe: (listener) => {
      updateListeners.push(listener)
      if (updatesState !== null) listener(updatesState)
      return () => {
        const index = updateListeners.indexOf(listener)
        if (index >= 0) updateListeners.splice(index, 1)
      }
    },
    open: () => request('updates.open', []),
    channel: () => request('updates.channel', []),
    setChannel: (channel) => request('updates.setChannel', [channel]),
    apply: () => request('updates.apply', []),
  }

  const appearance = {
    status: () => appearanceState !== null
      ? Promise.resolve(appearanceState)
      : request('appearance.status', []).then(state => {
        appearanceState = state
        applyAppearance(state)
        return state
      }),
    subscribe: (listener) => {
      appearanceListeners.push(listener)
      if (appearanceState !== null) listener(appearanceState)
      return () => {
        const index = appearanceListeners.indexOf(listener)
        if (index >= 0) appearanceListeners.splice(index, 1)
      }
    },
    setCornerRadiusScale: (scale) => request('appearance.setCornerRadiusScale', [scale]),
    setCustomCss: (css) => request('appearance.setCustomCss', [css]),
    setAppindicatorEnabled: (enabled) => request('appearance.setAppindicatorEnabled', [enabled]),
    setIconStyle: (style) => request('appearance.setIconStyle', [style]),
  }

  globalThis.dshDesktop = {
    protocolVersion: 1,
    updates,
    appearance,
    // Open one validated HTTP(S) link in the desktop default browser. The
    // shell's account sign-in flow (and any future native-prompted link)
    // uses this instead of window.open, which WebKit would route nowhere.
    openExternal: (uri) => {
      try {
        const url = new URL(String(uri), location.href)
        if (url.protocol === 'http:' || url.protocol === 'https:') {
          post({ type: 'open', uri: url.href })
        }
      } catch (_error) {
        // Malformed input is ignored; the page keeps its own error handling.
      }
    },
  }
  globalThis.__DSH_BRIDGE__ = {
    resolve(id, ok, value) {
      const entry = pending.get(id)
      if (entry === undefined) return
      pending.delete(id)
      if (ok) entry.resolve(value)
      else entry.reject(new Error(String(value)))
    },
    push(payload) {
      if (payload && payload.updates !== undefined) {
        updatesState = payload.updates
        for (const listener of updateListeners) listener(updatesState)
      }
      if (payload && payload.appearance !== undefined) {
        appearanceState = payload.appearance
        applyAppearance(appearanceState)
        for (const listener of appearanceListeners) listener(appearanceState)
      }
    },
  }
})()
