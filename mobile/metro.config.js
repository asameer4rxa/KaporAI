// Metro (the JavaScript bundler) keeps its caches in the system temp folder on C: by default.
// Point them at this project instead so everything stays on D:.
const fs = require('node:fs');
const path = require('node:path');
const { getDefaultConfig } = require('expo/metro-config');
const { FileStore } = require('metro-cache');

const config = getDefaultConfig(__dirname);
const cacheRoot = path.join(__dirname, '.metro-cache');
fs.mkdirSync(path.join(cacheRoot, 'file-map'), { recursive: true }); // Metro does not create this folder itself

config.cacheStores = [new FileStore({ root: path.join(cacheRoot, 'transform') })];
config.fileMapCacheDirectory = path.join(cacheRoot, 'file-map');

module.exports = config;
