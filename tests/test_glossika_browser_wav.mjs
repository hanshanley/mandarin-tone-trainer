import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { test } from 'node:test';
import GlossikaExamples from '../app/glossika_examples.js';


const BROWSERS = [
  '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
].filter(candidate => fs.existsSync(candidate));

test('browser decodes generated stereo IEEE float WAV without changing samples', {
  skip: BROWSERS.length ? false : 'No supported Chromium browser is installed',
  timeout: 30000,
}, async () => {
  const channels = [
    Float32Array.from([0, 0.125, -0.25, 0.5]),
    Float32Array.from([0, -0.375, 0.75, -1]),
  ];
  const wav = Buffer.from(GlossikaExamples.encodeFloatWav({
    numberOfChannels: 2,
    length: 4,
    sampleRate: 48000,
    getChannelData: channel => channels[channel],
  }));
  const server = http.createServer((request, response) => {
    if (request.url === '/slice.wav') {
      response.writeHead(200, {'Content-Type': 'audio/wav', 'Content-Length': wav.length});
      response.end(wav);
      return;
    }
    response.writeHead(200, {'Content-Type': 'text/html'});
    response.end('<!doctype html><title>Glossika WAV test</title>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'glossika-wav-browser-'));
  const browser = spawn(BROWSERS[0], [
    '--headless=new',
    '--remote-debugging-port=0',
    `--user-data-dir=${profile}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-background-networking',
    '--disable-component-update',
    '--disable-sync',
    '--mute-audio',
    `http://127.0.0.1:${server.address().port}/`,
  ], {stdio: ['ignore', 'ignore', 'pipe']});
  const waiting = new Map();
  let socket;
  let sequence = 0;
  const command = (method, params = {}, sessionId = undefined) => new Promise((resolve, reject) => {
    const id = ++sequence;
    waiting.set(id, {resolve, reject});
    socket.send(JSON.stringify({id, method, params, ...(sessionId ? {sessionId} : {})}));
  });
  try {
    const endpoint = await new Promise((resolve, reject) => {
      let stderr = '';
      const timer = setTimeout(() => reject(new Error('Browser debugging endpoint timed out')), 15000);
      browser.once('error', reject);
      browser.stderr.on('data', chunk => {
        stderr += chunk;
        const match = /DevTools listening on (ws:\/\/[^\s]+)/.exec(stderr);
        if (match) {
          clearTimeout(timer);
          resolve(match[1]);
        }
      });
    });
    socket = new WebSocket(endpoint);
    await new Promise((resolve, reject) => {
      socket.addEventListener('open', resolve, {once: true});
      socket.addEventListener('error', reject, {once: true});
    });
    socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      if (!message.id) return;
      const pending = waiting.get(message.id);
      if (!pending) return;
      waiting.delete(message.id);
      if (message.error) pending.reject(new Error(JSON.stringify(message.error)));
      else pending.resolve(message.result);
    });
    const {targetId} = await command('Target.createTarget', {
      url: `http://127.0.0.1:${server.address().port}/`,
    });
    const {sessionId} = await command('Target.attachToTarget', {targetId, flatten: true});
    await command('Runtime.enable', {}, sessionId);
    const result = await command('Runtime.evaluate', {
      expression: `(async()=>{
        const bytes=await fetch(${JSON.stringify(`http://127.0.0.1:${server.address().port}/slice.wav`)}).then(response=>response.arrayBuffer());
        const context=new AudioContext({sampleRate:48000});
        const decoded=await context.decodeAudioData(bytes);
        const output={
          sampleRate:decoded.sampleRate,
          channels:decoded.numberOfChannels,
          length:decoded.length,
          left:Array.from(decoded.getChannelData(0)),
          right:Array.from(decoded.getChannelData(1)),
        };
        await context.close();
        return output;
      })()`,
      awaitPromise: true,
      returnByValue: true,
    }, sessionId);
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.exception?.description || 'Browser WAV decode failed');
    }
    const decoded = result.result.value;
    assert.equal(decoded.sampleRate, 48000);
    assert.equal(decoded.channels, 2);
    assert.equal(decoded.length, 4);
    for (let index = 0; index < channels[0].length; index++) {
      assert.ok(Math.abs(decoded.left[index] - channels[0][index]) < 1e-7);
      assert.ok(Math.abs(decoded.right[index] - channels[1][index]) < 1e-7);
    }
  } finally {
    if (socket?.readyState === WebSocket.OPEN) socket.close();
    browser.kill('SIGTERM');
    await new Promise(resolve => browser.once('exit', resolve));
    await new Promise(resolve => server.close(resolve));
    fs.rmSync(profile, {recursive: true, force: true});
  }
});
