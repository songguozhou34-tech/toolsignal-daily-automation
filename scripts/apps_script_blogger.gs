const CONFIG = Object.freeze({
  indexUrl: 'https://songguozhou34-tech.github.io/toolsignal-daily-automation/'
});

function syncLatestToBlogger() {
  const indexResponse = UrlFetchApp.fetch(CONFIG.indexUrl, {muteHttpExceptions: true});
  if (indexResponse.getResponseCode() !== 200) {
    throw new Error('Index fetch failed: ' + indexResponse.getResponseCode());
  }
  const indexHtml = indexResponse.getContentText();
  const linkMatch = indexHtml.match(/href=["']\.\/([^"'#?]+\.html)["']/i);
  if (!linkMatch) throw new Error('No article link found on public index.');

  const articleUrl = CONFIG.indexUrl + linkMatch[1];
  const properties = PropertiesService.getScriptProperties();
  const postEmail = properties.getProperty('BLOGGER_POST_EMAIL');
  if (!postEmail) throw new Error('BLOGGER_POST_EMAIL script property is missing.');
  if (properties.getProperty('LAST_POST_URL') === articleUrl) {
    console.log('Already synced: ' + articleUrl);
    return;
  }

  const articleResponse = UrlFetchApp.fetch(articleUrl, {muteHttpExceptions: true});
  if (articleResponse.getResponseCode() !== 200) {
    throw new Error('Article fetch failed: ' + articleResponse.getResponseCode());
  }
  const articleHtml = articleResponse.getContentText();
  const titleMatch = articleHtml.match(/<title>([\s\S]*?)<\/title>/i);
  const mainMatch = articleHtml.match(/<main[^>]*>([\s\S]*?)<\/main>/i);
  if (!titleMatch || !mainMatch) throw new Error('Article HTML is missing title or main content.');

  const title = decodeHtml_(titleMatch[1].trim());
  const content = mainMatch[1]
    .replace(/href=["']\.\//gi, 'href="' + CONFIG.indexUrl)
    .replace(/<script[\s\S]*?<\/script>/gi, '') +
    '<p><a href="' + articleUrl + '">Canonical ToolSignal Daily page</a></p>';

  GmailApp.sendEmail(postEmail, title, 'Read the HTML version of this ToolSignal Daily post.', {
    htmlBody: content,
    name: 'ToolSignal Daily'
  });
  properties.setProperty('LAST_POST_URL', articleUrl);
  console.log('Sent latest article to Blogger email publishing as a draft: ' + title);
}

function installDailyTrigger() {
  ScriptApp.getProjectTriggers()
    .filter(function(trigger) { return trigger.getHandlerFunction() === 'syncLatestToBlogger'; })
    .forEach(function(trigger) { ScriptApp.deleteTrigger(trigger); });
  ScriptApp.newTrigger('syncLatestToBlogger')
    .timeBased()
    .everyDays(1)
    .atHour(10)
    .create();
  console.log('Daily trigger installed for the 10:00 Asia/Shanghai window.');
}

function decodeHtml_(value) {
  return value
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'");
}
