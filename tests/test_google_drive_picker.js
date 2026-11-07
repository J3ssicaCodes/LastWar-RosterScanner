const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {test} = require('node:test');
const {runInNewContext} = require('node:vm');
const {join} = require('node:path');

const source = readFileSync(join(__dirname, '..', 'google_drive_picker.js'), 'utf8');
const scope = 'https://www.googleapis.com/auth/drive.file';

function setup({configured = true} = {}) {
  const oauthRequests = [];
  const pickers = [];
  class PickerBuilder {
    addView() { return this; }
    setOAuthToken(token) { this.token = token; return this; }
    setDeveloperKey() { return this; }
    setAppId() { return this; }
    setOrigin(origin) { this.origin = origin; return this; }
    setCallback(callback) { this.callback = callback; return this; }
    build() {
      pickers.push(this);
      return {setVisible() {}};
    }
  }
  class DocsView {
    setMode() {}
    setIncludeFolders() {}
    setMimeTypes() {}
  }
  const context = {
    location: {origin: 'https://alliance.example'},
    fetch: async () => ({
      ok: true,
      json: async () => ({configured, client_id: 'app-client', api_key: 'picker-key', project_number: '1'}),
    }),
    gapi: {load: (_, {callback}) => callback()},
    google: {
      accounts: {oauth2: {
        initTokenClient(config) {
          return {requestAccessToken: overrides => oauthRequests.push({config, overrides})};
        },
        hasGrantedAllScopes: (response, requested) => response.scope === requested,
      }},
      picker: {
        PickerBuilder, DocsView,
        ViewId: {DOCS: 'docs'}, DocsViewMode: {LIST: 'list'},
        Action: {PICKED: 'picked', CANCEL: 'cancel'},
        Response: {DOCUMENTS: 'docs'}, Document: {ID: 'id', NAME: 'name', MIME_TYPE: 'mime'},
      },
    },
  };
  context.window = context;
  runInNewContext(source, context);
  return {
    picker: context.AllianceGoogleDrive, oauthRequests, pickers,
    configure: () => { configured = true; },
  };
}

test('each selection opens the Google account chooser and uses only the new token', async () => {
  const {picker, oauthRequests, pickers} = setup();
  await picker.prepare();
  const selected = [];
  for (const token of ['first-account-token', 'second-account-token']) {
    picker.choose({destination: 'vs', onSelected: file => selected.push(file)});
    const {config, overrides} = oauthRequests.at(-1);
    assert.equal(config.scope, scope);
    assert.equal(config.include_granted_scopes, false);
    assert.equal(overrides.prompt, 'select_account');
    config.callback({access_token: token, scope, expires_in: 3600});
    assert.equal(pickers.at(-1).token, token);
    assert.equal(pickers.at(-1).origin, 'https://alliance.example');
    pickers.at(-1).callback({action: 'picked', docs: [{id: 'selected-file', name: 'vs.mp4', mime: 'video/mp4'}]});
  }
  assert.equal(oauthRequests.length, 2);
  assert.deepEqual(selected.map(file => file.access_token), ['first-account-token', 'second-account-token']);
});

test('a denied authorization leaves the chooser available for another attempt', async () => {
  const {picker, oauthRequests, pickers} = setup();
  await picker.prepare();
  const errors = [];
  picker.choose({destination: 'players', onError: error => errors.push(error.message)});
  oauthRequests[0].config.callback({access_token: 'incomplete-grant', scope: 'openid'});
  assert.equal(errors.length, 1);
  assert.equal(pickers.length, 0);
  picker.choose({destination: 'players'});
  assert.equal(oauthRequests.length, 2);
});

test('the picker can retry configuration after an initial failure', async () => {
  const {picker, configure} = setup({configured: false});
  await assert.rejects(picker.prepare(), /one-time setup/);
  configure();
  await picker.prepare();
  assert.equal(picker.isReady(), true);
  assert.equal(picker.error(), '');
});
