/**
 * Ad blockers treat filenames that end in `_ad.js` / `-ad.js` as ads.
 * Vite hashes can produce those endings by chance and then block lazy pages.
 */
export const AD_BLOCK_BAIT_JS = /[_/-]ads?\.js$/i

/** Rolldown-safe suffix so hashed names never end in `_ad.js` / `-ad.js`. */
export const JS_CHUNK_FILE_NAME_PATTERN = 'assets/[name]-[hash]x.js'

export function isAdBlockBaitJsFileName(fileName: string): boolean {
  return AD_BLOCK_BAIT_JS.test(fileName)
}
