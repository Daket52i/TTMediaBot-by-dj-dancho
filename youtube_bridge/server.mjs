import http from 'node:http';
import fs from 'node:fs/promises';
import { statSync } from 'node:fs';
import { URL } from 'node:url';
import { Innertube, UniversalCache, Platform } from 'youtubei.js';

const HOST = process.env.YOUTUBE_BRIDGE_HOST || '127.0.0.1';
const PORT = Number(process.env.YOUTUBE_BRIDGE_PORT || 4417);
const POT_URL = process.env.POT_PROVIDER_URL || 'http://127.0.0.1:4416/get_pot';
const USER_AGENT = process.env.YOUTUBE_BRIDGE_USER_AGENT ||
  'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36';

// YouTube.js 18 requires an evaluator to decipher player signatures/nsig.
Platform.shim.eval = async (data) => new Function(data.output)();

let sessionCache = { key: null, session: null };

function json(res, status, body) {
  const data = Buffer.from(JSON.stringify(body));
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': data.length
  });
  res.end(data);
}

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  if (!chunks.length) return {};
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

function cookieFileKey(cookieFile) {
  if (!cookieFile) return 'anonymous';
  try {
    const stat = statSync(cookieFile);
    return `${cookieFile}:${stat.mtimeMs}:${stat.size}`;
  } catch {
    return `${cookieFile}:missing`;
  }
}

async function netscapeCookiesToHeader(cookieFile) {
  if (!cookieFile) return '';
  let text;
  try {
    text = await fs.readFile(cookieFile, 'utf8');
  } catch (error) {
    if (error?.code === 'ENOENT') return '';
    throw error;
  }

  const cookies = [];
  for (const rawLine of text.split(/\r?\n/)) {
    let line = rawLine.trim();
    if (!line) continue;
    if (line.startsWith('#HttpOnly_')) line = line.slice('#HttpOnly_'.length);
    else if (line.startsWith('#')) continue;

    const parts = line.split('\t');
    if (parts.length < 7) continue;
    const domain = parts[0].toLowerCase();
    if (!domain.includes('youtube.com') && !domain.includes('google.com')) continue;
    const name = parts[5];
    const value = parts.slice(6).join('\t');
    if (name) cookies.push(`${name}=${value}`);
  }
  return cookies.join('; ');
}

async function getSession(cookieFile) {
  const key = cookieFileKey(cookieFile);
  if (sessionCache.key === key && sessionCache.session) return sessionCache.session;

  const cookie = await netscapeCookiesToHeader(cookieFile);
  const session = await Innertube.create({
    cookie: cookie || undefined,
    user_agent: USER_AGENT,
    cache: new UniversalCache(true),
    enable_session_cache: true,
    generate_session_locally: true,
    retrieve_player: true
  });

  sessionCache = { key, session };
  return session;
}

async function getPoToken(videoId) {
  try {
    const response = await fetch(POT_URL, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ content_binding: videoId })
    });
    if (!response.ok) {
      const body = await response.text();
      console.warn(`[youtube-bridge] POT provider HTTP ${response.status}: ${body.slice(0, 300)}`);
      return undefined;
    }
    const data = await response.json();
    return data.poToken || data.po_token || undefined;
  } catch (error) {
    console.warn(`[youtube-bridge] POT provider unavailable: ${error.message}`);
    return undefined;
  }
}

function extractVideoId(input) {
  if (!input) return null;
  if (/^[A-Za-z0-9_-]{11}$/.test(input)) return input;
  try {
    const url = new URL(input);
    if (url.hostname === 'youtu.be') return url.pathname.split('/').filter(Boolean)[0] || null;
    if (url.searchParams.get('v')) return url.searchParams.get('v');
    const shorts = url.pathname.match(/^\/shorts\/([^/?]+)/);
    if (shorts) return shorts[1];
  } catch {}
  return null;
}

function extractPlaylistId(input) {
  if (!input) return null;
  try {
    const url = new URL(input);
    return url.searchParams.get('list');
  } catch {
    return null;
  }
}

function textValue(value) {
  if (!value) return '';
  if (typeof value === 'string') return value;
  if (typeof value.toString === 'function') return value.toString();
  return '';
}

function ownerName(info) {
  const basic = info?.basic_info || {};
  return textValue(basic.author) || textValue(basic.channel?.name) || '';
}

function infoPayload(info, videoId) {
  const basic = info?.basic_info || {};
  return {
    id: basic.id || videoId,
    videoId: basic.id || videoId,
    title: basic.title || '',
    uploader: ownerName(info),
    duration: basic.duration || 0,
    is_live: Boolean(basic.is_live || basic.is_live_content),
    webpage_url: `https://www.youtube.com/watch?v=${basic.id || videoId}`,
    http_headers: { 'User-Agent': USER_AGENT }
  };
}

function formatPayload(format) {
  return {
    url: format.url,
    itag: format.itag,
    mime_type: format.mime_type,
    bitrate: format.bitrate,
    average_bitrate: format.average_bitrate,
    content_length: format.content_length,
    quality: format.quality,
    quality_label: format.quality_label,
    audio_quality: format.audio_quality,
    audio_sample_rate: format.audio_sample_rate,
    audio_channels: format.audio_channels,
    has_audio: format.has_audio,
    has_video: format.has_video
  };
}

async function getPlayableInfo(session, videoId, client) {
  if (client === 'YTMUSIC') {
    return session.music.getInfo(videoId);
  }
  return session.getBasicInfo(videoId, { client });
}

function playabilityDescription(info) {
  const status = info?.playability_status?.status || 'UNKNOWN';
  const reason = info?.playability_status?.reason || '';
  return reason ? `${status}: ${reason}` : status;
}

async function resolveFormat(session, videoId, requestedClient, formatOptions) {
  // WEB is SABR-only for many videos in 2026. MWEB still exposes classic
  // adaptive formats and is the preferred web playback client here.
  const clients = requestedClient === 'YTMUSIC'
    ? ['YTMUSIC', 'MWEB']
    : ['MWEB'];
  const failures = [];

  for (const client of clients) {
    try {
      // Current web/mweb/web_music enforcement requires the PO token for GVS,
      // not for the player request. Fetch formats first, then bind the token
      // to the final GoogleVideo URL during deciphering.
      const info = await getPlayableInfo(session, videoId, client);
      if (!info?.streaming_data) {
        throw new Error(`no streaming data (${playabilityDescription(info)})`);
      }

      const format = info.chooseFormat(formatOptions);
      if (!session.session.player) {
        throw new Error('YouTube player is unavailable');
      }

      const poToken = await getPoToken(videoId);
      session.session.player.po_token = poToken;
      format.url = await format.decipher(session.session.player);

      if (!format.url) {
        throw new Error('decipher returned an empty stream URL');
      }

      console.log(`[youtube-bridge] resolved ${videoId} with client=${client} itag=${format.itag}`);
      return { info, format, client };
    } catch (error) {
      const message = error?.message || String(error);
      failures.push(`${client}: ${message}`);
      console.warn(`[youtube-bridge] ${videoId} client=${client} failed: ${message}`);
    }
  }

  throw new Error(`Unable to resolve stream for ${videoId}; ${failures.join(' | ')}`);
}

async function resolveTrack(body) {
  const videoId = body.video_id || extractVideoId(body.url);
  if (!videoId) throw new Error('Invalid YouTube URL or video ID');
  const session = await getSession(body.cookie_file);
  const requestedClient = body.client === 'YTMUSIC' ? 'YTMUSIC' : 'MWEB';

  const { info, format, client } = await resolveFormat(session, videoId, requestedClient, {
    type: 'audio',
    quality: 'best',
    format: 'any'
  });

  const metadata = infoPayload(info, videoId);
  return {
    ...metadata,
    ...formatPayload(format),
    client,
    format: 'mp3',
    http_headers: { 'User-Agent': USER_AGENT }
  };
}

async function getInfo(body) {
  const videoId = body.video_id || extractVideoId(body.url);
  if (!videoId) throw new Error('Invalid YouTube URL or video ID');
  const session = await getSession(body.cookie_file);
  const client = body.client === 'YTMUSIC' ? 'YTMUSIC' : 'MWEB';
  const info = await getPlayableInfo(session, videoId, client);
  return {
    ...infoPayload(info, videoId),
    playability_status: info?.playability_status?.status || '',
    playability_reason: info?.playability_status?.reason || ''
  };
}

async function getPlaylist(body) {
  const playlistId = body.playlist_id || extractPlaylistId(body.url);
  if (!playlistId) throw new Error('Invalid YouTube playlist URL or ID');
  const session = await getSession(body.cookie_file);
  const playlist = await session.getPlaylist(playlistId);
  const items = playlist?.items || playlist?.videos || [];
  return {
    id: playlistId,
    title: textValue(playlist?.info?.title),
    uploader: textValue(playlist?.info?.author?.name),
    entries: items.map((item) => {
      const id = item.id || item.video_id || item.content_id;
      return {
        id,
        videoId: id,
        title: textValue(item.title) || textValue(item.metadata?.title),
        uploader: textValue(item.author?.name) || textValue(item.metadata?.author?.name),
        webpage_url: id ? `https://www.youtube.com/watch?v=${id}` : ''
      };
    }).filter((item) => item.id)
  };
}

async function getDownloadPlan(body) {
  const videoId = body.video_id || extractVideoId(body.url);
  if (!videoId) throw new Error('Invalid YouTube URL or video ID');
  const session = await getSession(body.cookie_file);
  const requestedClient = body.client === 'YTMUSIC' ? 'YTMUSIC' : 'MWEB';

  if (!body.video) {
    const { format: audio, client } = await resolveFormat(session, videoId, requestedClient, {
      type: 'audio', quality: 'best', format: 'any'
    });
    return {
      audio: formatPayload(audio),
      client,
      http_headers: { 'User-Agent': USER_AGENT }
    };
  }

  let videoResult;
  try {
    videoResult = await resolveFormat(session, videoId, requestedClient, {
      type: 'video', quality: 'best', format: 'mp4'
    });
  } catch {
    videoResult = await resolveFormat(session, videoId, requestedClient, {
      type: 'video', quality: 'best', format: 'any'
    });
  }

  let audioResult;
  try {
    audioResult = await resolveFormat(session, videoId, requestedClient, {
      type: 'audio', quality: 'best', format: 'mp4'
    });
  } catch {
    audioResult = await resolveFormat(session, videoId, requestedClient, {
      type: 'audio', quality: 'best', format: 'any'
    });
  }

  return {
    video: formatPayload(videoResult.format),
    audio: formatPayload(audioResult.format),
    client: `${videoResult.client}/${audioResult.client}`,
    http_headers: { 'User-Agent': USER_AGENT }
  };
}

const server = http.createServer(async (req, res) => {
  try {
    if (req.method === 'GET' && req.url === '/health') {
      return json(res, 200, { ok: true, version: '2' });
    }
    if (req.method !== 'POST') return json(res, 404, { error: 'Not found' });

    const body = await readBody(req);
    if (req.url === '/resolve') return json(res, 200, await resolveTrack(body));
    if (req.url === '/info') return json(res, 200, await getInfo(body));
    if (req.url === '/playlist') return json(res, 200, await getPlaylist(body));
    if (req.url === '/download-plan') return json(res, 200, await getDownloadPlan(body));
    return json(res, 404, { error: 'Not found' });
  } catch (error) {
    console.error('[youtube-bridge]', error);
    return json(res, 500, { error: error?.message || String(error) });
  }
});

server.listen(PORT, HOST, () => {
  console.log(`[youtube-bridge] listening on http://${HOST}:${PORT}`);
});
