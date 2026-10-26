const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const ROOT = process.cwd();
const read = p => JSON.parse(fs.readFileSync(path.join(ROOT,p),'utf8').replace(/^\uFEFF/,''));
const esc = s => String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main';
function p(text, options={}) {
 const {size=22,bold=false,style='',align='left',after=100,before=0,indent=false,line=340,font='宋体'}=options;
 return `<w:p><w:pPr>${style?`<w:pStyle w:val="${style}"/>`:''}<w:jc w:val="${align}"/><w:spacing w:before="${before}" w:after="${after}" w:line="${line}" w:lineRule="auto"/>${indent?'<w:ind w:firstLine="440"/>':''}<w:widowControl/></w:pPr><w:r><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="${font}"/><w:color w:val="000000"/><w:sz w:val="${size}"/>${bold?'<w:b/>':''}</w:rPr><w:t xml:space="preserve">${esc(text)}</w:t></w:r></w:p>`;
}
const h = text => p(text,{size:28,bold:true,style:'Heading1',font:'微软雅黑',before:60,after:160});
const sub = text => p(text,{size:24,bold:true,style:'Heading2',font:'微软雅黑',before:100,after:80});
const body = text => p(text,{indent:true});
const caption = text => p(text,{size:19,align:'center',after:100,line:280});
function table(headers, rows, widths) {
 const total=widths.reduce((a,b)=>a+b,0);
 const row=(cells,header=false,index=0)=>`<w:tr><w:trPr><w:cantSplit/>${header?'<w:tblHeader/>':''}</w:trPr>${cells.map((cell,j)=>`<w:tc><w:tcPr><w:tcW w:w="${widths[j]}" w:type="dxa"/><w:shd w:fill="${header?'DBE6F2':index%2?'F7F9FC':'FFFFFF'}"/><w:vAlign w:val="center"/></w:tcPr>${p(cell,{size:20,bold:header,after:0,line:280})}</w:tc>`).join('')}</w:tr>`;
 return `<w:tbl><w:tblPr><w:tblW w:w="${total}" w:type="dxa"/><w:tblLayout w:type="fixed"/><w:tblBorders>${['top','bottom','left','right','insideH','insideV'].map(k=>`<w:${k} w:val="single" w:sz="5" w:color="D9D9D9"/>`).join('')}</w:tblBorders><w:tblCellMar><w:top w:w="95" w:type="dxa"/><w:left w:w="110" w:type="dxa"/><w:bottom w:w="95" w:type="dxa"/><w:right w:w="110" w:type="dxa"/></w:tblCellMar></w:tblPr><w:tblGrid>${widths.map(w=>`<w:gridCol w:w="${w}"/>`).join('')}</w:tblGrid>${row(headers,true)}${rows.map((r,i)=>row(r,false,i)).join('')}</w:tbl>`+p('',{size:4,line:80,after:0});
}
const images=[];
function figure(file,widthInches,description) {
 const bytes=fs.readFileSync(path.join(ROOT,file));
 const w=bytes.readUInt32BE(16), ht=bytes.readUInt32BE(20), id=images.length+1;
 const cx=Math.round(widthInches*914400),cy=Math.round(cx*ht/w);
 images.push({name:`image${id}.png`,bytes});
 return `<w:p><w:pPr><w:jc w:val="center"/><w:spacing w:after="80"/></w:pPr><w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="${cx}" cy="${cy}"/><wp:docPr id="${id}" name="Figure ${id}" descr="${esc(description)}"/><wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic><pic:nvPicPr><pic:cNvPr id="${id}" name="${esc(description)}"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip r:embed="img${id}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="${cx}" cy="${cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>`;
}
const footer=`<?xml version="1.0" encoding="UTF-8"?><w:ftr xmlns:w="${NS}"><w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:rPr><w:sz w:val="18"/></w:rPr><w:t>第 </w:t></w:r><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r><w:r><w:t> 页</w:t></w:r></w:p></w:ftr>`;
function packageDoc(bodyXml,out) {
 const section='<w:sectPr><w:footerReference w:type="default" r:id="footer"/><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1050" w:right="1253" w:bottom="1000" w:left="1253" w:header="300" w:footer="450"/></w:sectPr>';
 const entries={
 '[Content_Types].xml':`<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/><Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/></Types>`,
 '_rels/.rels':'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="doc" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
 'word/_rels/document.xml.rels':`<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="styles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/><Relationship Id="footer" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>${images.map((img,i)=>`<Relationship Id="img${i+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/${img.name}"/>`).join('')}</Relationships>`,
 'word/styles.xml':`<?xml version="1.0" encoding="UTF-8"?><w:styles xmlns:w="${NS}"><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="宋体"/><w:color w:val="000000"/><w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults><w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>${['Title','Heading1','Heading2'].map((name,i)=>`<w:style w:type="paragraph" w:styleId="${name}"><w:name w:val="${name}"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/>${i?`<w:outlineLvl w:val="${i-1}"/>`:''}</w:pPr><w:rPr><w:b/><w:color w:val="000000"/></w:rPr></w:style>`).join('')}</w:styles>`,
 'word/footer1.xml':footer,
 'word/document.xml':`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="${NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><w:body>${bodyXml}${section}</w:body></w:document>`
 };
 images.forEach(img=>entries['word/media/'+img.name]=img.bytes);
 const crcTable=Array.from({length:256},(_,n)=>{let c=n;for(let k=0;k<8;k++)c=c&1?0xedb88320^(c>>>1):c>>>1;return c>>>0});
 const crc=buf=>{let c=0xffffffff;for(const b of buf)c=crcTable[(c^b)&255]^(c>>>8);return (c^0xffffffff)>>>0};
 let chunks=[],central=[],offset=0;
 for(const [name,value] of Object.entries(entries)) {
  const n=Buffer.from(name), b=Buffer.isBuffer(value)?value:Buffer.from(value), compressed=zlib.deflateRawSync(b), checksum=crc(b);
  const local=Buffer.alloc(30);local.writeUInt32LE(0x04034b50);local.writeUInt16LE(20,4);local.writeUInt16LE(8,8);local.writeUInt32LE(checksum,14);local.writeUInt32LE(compressed.length,18);local.writeUInt32LE(b.length,22);local.writeUInt16LE(n.length,26);
  const cd=Buffer.alloc(46);cd.writeUInt32LE(0x02014b50);cd.writeUInt16LE(20,4);cd.writeUInt16LE(20,6);cd.writeUInt16LE(8,10);cd.writeUInt32LE(checksum,16);cd.writeUInt32LE(compressed.length,20);cd.writeUInt32LE(b.length,24);cd.writeUInt16LE(n.length,28);cd.writeUInt32LE(offset,42);
  chunks.push(local,n,compressed);central.push(cd,n);offset+=local.length+n.length+compressed.length;
 }
 const cd=Buffer.concat(central),end=Buffer.alloc(22);end.writeUInt32LE(0x06054b50);end.writeUInt16LE(Object.keys(entries).length,8);end.writeUInt16LE(Object.keys(entries).length,10);end.writeUInt32LE(cd.length,12);end.writeUInt32LE(offset,16);
 fs.mkdirSync(path.dirname(out),{recursive:true});fs.writeFileSync(out,Buffer.concat([...chunks,cd,end]));
}

module.exports = {p,h,sub,body,caption,table,figure,packageDoc};
