-- Only plain, passive content reaches the EPUB. Kavita's web reader puts book
-- pages into the DOM without sanitising them (0.9.1, bypassSecurityTrustHtml),
-- and pandoc would otherwise pass raw HTML, attributes and link targets through
-- as written.

-- Images: only embedded raster images (no SVG, which can carry script). Any
-- other image (a URL or a path) becomes its alt text, so pandoc never fetches
-- or reads a file for it.
local SAFE_IMAGE = {
  ["image/png"] = true, ["image/jpeg"] = true, ["image/jpg"] = true,
  ["image/gif"] = true, ["image/webp"] = true,
}
-- Raw HTML: only these bare tags (no attributes), e.g. <sub>2</sub> or <br>.
local SIMPLE_TAG = {
  br = true, sub = true, sup = true, b = true, i = true, em = true,
  strong = true, u = true, s = true, kbd = true, small = true,
}

-- Event handlers, inline styles (url(...) fetches) and stray src/href attributes.
local function scrub(el)
  local drop = {}
  for key, _ in pairs(el.attributes) do
    local k = key:lower()
    if k:sub(1, 2) == "on" or k == "style" or k == "src" or k == "href" or k:sub(1, 5) == "xmlns" then
      table.insert(drop, key)
    end
  end
  for _, key in ipairs(drop) do
    el.attributes[key] = nil
  end
  return el
end

function Image(el)
  local mime = el.src:match("^data:([%w/+.-]+);base64,")
  if mime and SAFE_IMAGE[mime:lower()] then
    return scrub(el)
  end
  return pandoc.Span(el.caption)
end

-- Links: web, mail and in-book targets only; anything else (javascript:, data:,
-- relative files) keeps its text and loses the link.
function Link(el)
  local target = el.target:lower()
  if target:match("^https?://") or target:match("^mailto:") or target:sub(1, 1) == "#" then
    return scrub(el)
  end
  return pandoc.Span(el.content)
end

function RawInline(el)
  local tag = el.text:lower():match("^<%s*/?%s*(%a+)%s*/?%s*>$")
  if el.format:match("^html") and tag and SIMPLE_TAG[tag] then
    return el
  end
  return {}
end

function RawBlock(el)
  return {}
end

Span = scrub
Div = scrub
Header = scrub
Code = scrub
CodeBlock = scrub
Table = scrub
Figure = scrub
