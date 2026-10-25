'use strict';
(() => {
  const data = JSON.parse(document.getElementById('app-i18n').textContent);
  const language = data.language === 'pt-BR' ? 'pt-BR' : 'en';
  const catalog = data.catalog;
  const patterns = Object.entries(catalog.patterns).map(([source, target]) => {
    const escaped = source.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return [new RegExp('^' + escaped.replace(/\\\{\d+\\\}/g, '(.*?)') + '$'), target];
  });
  function translate(text) {
    if (language !== 'en' || !text) return text;
    const value = String(text).trim().replace(/\s+/g, ' ');
    let translated = catalog.strings[value];
    if (translated === undefined) for (const [pattern, target] of patterns) {
      const match = value.match(pattern) || (value + ' ').match(pattern);
      if (match) { translated = target.replace(/\{(\d+)\}/g, (_, index) => match[Number(index) + 1]); break; }
    }
    if (translated === undefined) {
      const sentences=value.split(/(?<=[.!?])\s+/);
      if(sentences.length<2) return text;
      const parts=sentences.map(translate);
      if(parts.every((part,index)=>part===sentences[index])) return text;
      translated=parts.join(' ');
    }
    return String(text).match(/^\s*/)[0] + translated.trim() + String(text).match(/\s*$/)[0];
  }
  const excluded = 'script,style,svg,pre,code,[data-i18n-skip]';
  const attributes = ['aria-label', 'title', 'placeholder', 'alt'];
  function localize(node) {
    if (node.nodeType === Node.TEXT_NODE) {
      if (!node.parentElement?.closest(excluded)) {
        const translated = translate(node.data);
        if (translated !== node.data) node.data = translated;
      }
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE || node.closest(excluded)) return;
    if(node.matches('meta[name="description"]')) node.content=translate(node.content);
    for (const attribute of attributes) if (node.hasAttribute(attribute)) {
      const value = node.getAttribute(attribute), translated = translate(value);
      if (translated !== value) node.setAttribute(attribute, translated);
    }
    for (const child of node.childNodes) localize(child);
  }
  document.documentElement.lang = language;
  window.AcervoI18n = {language, translate, localize};
  localize(document.documentElement);
  const selector = document.getElementById('language');
  if (selector) selector.value = language;
  if (language !== 'en') return;
  const observer = new MutationObserver(records => {
    observer.disconnect();
    const nodes = new Set();
    for (const record of records) {
      if (record.type === 'childList') for (const node of record.addedNodes) nodes.add(node);
      else nodes.add(record.target);
    }
    for (const node of nodes) if (node.isConnected) localize(node);
    observer.observe(document.body, options);
  });
  const options = {subtree:true, childList:true, characterData:true, attributes:true, attributeFilter:attributes};
  observer.observe(document.body, options);
})();
