// Runs the Expo CLI for this project with its state folder kept inside the project
// (never under C:\Users) and with Expo's usage telemetry switched off.
// Used by the npm scripts in package.json; works from any current directory.
const { spawnSync } = require('node:child_process');
const path = require('node:path');

const projectRoot = path.join(__dirname, '..');
process.env.__UNSAFE_EXPO_HOME_DIRECTORY ||= path.join(projectRoot, '.expo-home');
process.env.EXPO_NO_TELEMETRY ||= '1';

const cli = require.resolve('expo/bin/cli');
const result = spawnSync(process.execPath, [cli, ...process.argv.slice(2)], { stdio: 'inherit', cwd: projectRoot });
process.exit(result.status ?? 1);
