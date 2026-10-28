-- Keep only images embedded as data: URIs. Any other image (remote URL or a
-- path) becomes its alt text, so pandoc never fetches or reads a file for it.
function Image(el)
  if el.src:sub(1, 5) == "data:" then
    return el
  end
  return pandoc.Span(el.caption)
end

-- Raw HTML <img> tags would reach the EPUB as is: drop them.
local function drop_img(el)
  if el.format:match("html") and el.text:lower():find("<img", 1, true) then
    return {}
  end
end

RawInline = drop_img
RawBlock = drop_img
