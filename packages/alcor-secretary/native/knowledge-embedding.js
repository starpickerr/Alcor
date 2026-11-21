// Use the OS language assets already available to this account.
ObjC.import('Foundation')
ObjC.import('NaturalLanguage')
function normalized(values) {
  const size = Math.hypot(...values)
  return size ? values.map((v) => v / size) : values
}
function run() {
  const input = $.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile
  const { language, texts } = JSON.parse(
    ObjC.unwrap($.NSString.alloc.initWithDataEncoding(input, $.NSUTF8StringEncoding))
  )
  const sentence = $.NLEmbedding.sentenceEmbeddingForLanguage(language)
  if (!sentence.dimension) return 'null'
  const words = language === 'zh-Hans' ? $.NLEmbedding.wordEmbeddingForLanguage(language) : null
  const segmenter = words?.dimension && new Intl.Segmenter('zh', { granularity: 'word' })
  const model =
    'apple-sentence/' +
    language +
    '/' +
    sentence.revision +
    '/' +
    sentence.dimension +
    (segmenter ? '/words-v1/' + words.revision + '/' + words.dimension : '')
  const vectors = texts.map((text) => {
    const representation = ObjC.deepUnwrap(sentence.vectorForString(text))
    if (!representation || !segmenter) return representation || null
    const mean = Array(Number(words.dimension)).fill(0)
    for (const { segment, isWordLike } of segmenter.segment(text)) {
      if (!isWordLike || segment.length < 2) continue
      const v = ObjC.deepUnwrap(words.vectorForString(segment))
      if (v) for (let i = 0; i < v.length; i++) mean[i] += v[i]
    }
    return normalized(representation)
      .map((x) => x * 0.35)
      .concat(normalized(mean))
  })
  return JSON.stringify({ model, vectors })
}
