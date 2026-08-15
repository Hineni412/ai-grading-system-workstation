import type { Plugin } from 'vite'

/**
 * Ad blockers treat `_ad.js` / `-ad.js` as ads. Vite hashes can produce those
 * endings by chance, which then blocks lazy workspace pages.
 */
const AD_SEGMENT = /[_/-]ads?(?=[-_.]|$)/gi

export function neutralizeAdBlockBaitFileName(fileName: string): string {
  return fileName.replace(AD_SEGMENT, (match) => `${match[0]}xx`)
}

function rewriteSource(source: string, renames: Map<string, string>): string {
  let next = source
  for (const [from, to] of renames) {
    next = next.replaceAll(from, to)
  }
  return next
}

export function neutralizeAdBlockBaitFilenames(): Plugin {
  return {
    name: 'neutralize-adblock-bait-filenames',
    enforce: 'post',
    generateBundle(_options, bundle) {
      const renames = new Map<string, string>()
      for (const fileName of Object.keys(bundle)) {
        const next = neutralizeAdBlockBaitFileName(fileName)
        if (next === fileName) continue
        if (Object.hasOwn(bundle, next) || [...renames.values()].includes(next)) {
          throw new Error(`ad-block filename rewrite collided: ${fileName} -> ${next}`)
        }
        renames.set(fileName, next)
      }
      if (renames.size === 0) return
      for (const file of Object.values(bundle)) {
        if (file.type === 'chunk') {
          file.code = rewriteSource(file.code, renames)
        } else if (typeof file.source === 'string') {
          file.source = rewriteSource(file.source, renames)
        }
      }
      for (const [from, to] of renames) {
        const file = bundle[from]
        if (!file) continue
        file.fileName = to
        bundle[to] = file
        delete bundle[from]
      }
    },
  }
}
