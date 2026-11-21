// Run by the system JavaScript for Automation interpreter with one private temporary file.
ObjC.import('Foundation')
ObjC.import('PDFKit')
ObjC.import('Vision')
ObjC.import('AppKit')

function recognize(handler) {
  const request = $.VNRecognizeTextRequest.alloc.init
  request.recognitionLanguages = ['zh-Hans', 'en-US']
  request.usesLanguageCorrection = true
  const error = Ref()
  if (!handler.performRequestsError($.NSArray.arrayWithObject(request), error)) return ''
  const rows = request.results
  const text = []
  for (let i = 0; i < rows.count; i++) {
    const candidates = rows.objectAtIndex(i).topCandidates(1)
    if (candidates.count) text.push(ObjC.unwrap(candidates.firstObject.string))
  }
  return text.join('\n')
}

function run([path, kind]) {
  const url = $.NSURL.fileURLWithPath(path)
  if (kind === 'image') {
    return JSON.stringify([recognize($.VNImageRequestHandler.alloc.initWithURLOptions(url, $({})))])
  }
  const pdf = $.PDFDocument.alloc.initWithURL(url)
  if (pdf.isLocked || !pdf.pageCount) throw Error('Unreadable PDF')
  const pages = []
  let scanPages = 0
  for (let i = 0; i < Math.min(pdf.pageCount, 500); i++) {
    const page = pdf.pageAtIndex(i)
    let text = ObjC.unwrap(page.string) || ''
    if (!text.trim() && scanPages++ < 12) {
      const preview = page.thumbnailOfSizeForBox($.NSMakeSize(1400, 1800), 0)
      text = recognize($.VNImageRequestHandler.alloc.initWithDataOptions(preview.TIFFRepresentation, $({})))
    }
    pages.push(text)
  }
  return JSON.stringify({ pages, truncated: pdf.pageCount > pages.length })
}
