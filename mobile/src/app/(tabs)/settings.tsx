import { useEffect, useState } from 'react';
import { ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';

import { Banner, Button, Chip, Page, SectionTitle, styles as ui } from '@/components/ui';
import { createApi } from '@/lib/api';
import { cloudAvailable, defaultServerUrl, useSettings } from '@/lib/settings';
import type { Profile } from '@/lib/types';
import { colors, space } from '@/theme';

type Status = { kind: 'good' | 'error' | 'info'; text: string };

// The profile fields the stylist reads. Lists are typed as comma-separated text.
const TEXT_FIELDS: { key: string; label: string; placeholder: string }[] = [
  { key: 'style_of_dress', label: 'Style of dress', placeholder: 'menswear, womenswear, androgynous ...' },
  { key: 'body_build', label: 'Body build', placeholder: 'e.g. athletic, broad shoulders' },
  { key: 'fit_preferences', label: 'Fit preferences', placeholder: 'e.g. relaxed tops, tapered trousers' },
  { key: 'style_notes', label: 'Style notes', placeholder: 'e.g. earth tones, no big logos' },
];
const LIST_FIELDS: { key: string; label: string; placeholder: string }[] = [
  { key: 'favourite_colours', label: 'Favourite colours', placeholder: 'navy, olive, cream' },
  { key: 'colours_to_avoid', label: 'Colours to avoid', placeholder: 'pink, neon' },
];
const SIZE_FIELDS = ['top', 'bottom', 'shoes'];

const asText = (value: unknown) => (value === null || value === undefined ? '' : String(value));
const asList = (value: unknown) => (Array.isArray(value) ? value.join(', ') : asText(value));

export default function SettingsScreen() {
  const { api, ready, mode, setMode, signedInAs, signOut, serverUrl, apiKey, save } = useSettings();
  const [url, setUrl] = useState(serverUrl);
  const [key, setKey] = useState(apiKey);
  const [connection, setConnection] = useState<Status | null>(null);
  const [testing, setTesting] = useState(false);

  const [profile, setProfile] = useState<Profile | null>(null);
  const [profileStatus, setProfileStatus] = useState<Status | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setUrl(serverUrl);
    setKey(apiKey);
  }, [serverUrl, apiKey]);

  useEffect(() => {
    if (!ready) return;
    setConnection(null);
    api
      .profile()
      .then((data) => {
        setProfile(data);
        setProfileStatus(null);
      })
      .catch((problem: Error) => setProfileStatus({ kind: 'error', text: problem.message }));
  }, [api, ready]);

  async function checkPc() {
    setTesting(true);
    setConnection(null);
    try {
      const pc = await api.status();
      setConnection({ kind: pc.online ? 'good' : 'info', text: pc.detail });
    } catch (problem) {
      setConnection({ kind: 'error', text: (problem as Error).message });
    } finally {
      setTesting(false);
    }
  }

  async function saveAndTest() {
    setTesting(true);
    setConnection(null);
    try {
      await save(url, key);
      // test with the values just typed, not the ones from the previous render
      const health = await createApi(url.trim() || defaultServerUrl(), key.trim()).health();
      const parts = [`${health.items} item(s) in the wardrobe`];
      if (!health.models_ready) parts.push('vision models still loading');
      if (!health.ollama.ok) parts.push('Ollama is not reachable on the PC');
      setConnection({ kind: health.ollama.ok ? 'good' : 'error', text: `Connected. ${parts.join('; ')}.` });
    } catch (problem) {
      setConnection({ kind: 'error', text: (problem as Error).message });
    } finally {
      setTesting(false);
    }
  }

  function setField(fieldKey: string, value: unknown) {
    setProfile((current) => ({ ...(current ?? {}), [fieldKey]: value }));
  }

  async function saveProfile() {
    if (!profile) return;
    setSaving(true);
    try {
      setProfile(await api.saveProfile(profile));
      setProfileStatus({ kind: 'good', text: 'Profile saved. The stylist uses it from the next request.' });
    } catch (problem) {
      setProfileStatus({ kind: 'error', text: (problem as Error).message });
    } finally {
      setSaving(false);
    }
  }

  const sizes = (profile?.sizes ?? {}) as Record<string, unknown>;

  return (
    <Page>
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <SectionTitle>Connection</SectionTitle>
        <View style={styles.modes}>
          {cloudAvailable ? <Chip label="Cloud" selected={mode === 'cloud'} onPress={() => setMode('cloud')} /> : null}
          <Chip label="Straight to the PC" selected={mode === 'direct'} onPress={() => setMode('direct')} />
        </View>

        {mode === 'cloud' ? (
          <View>
            <Text style={ui.hint}>
              Your wardrobe is read from your private cloud copy, so it works on any network. New photos and outfit requests are
              handled by your PC whenever it is on.
            </Text>
            <Text style={styles.account}>Signed in as {signedInAs ?? '...'}</Text>
            <Button label="Is my PC on?" icon="desktop-outline" busy={testing} onPress={checkPc} style={styles.button} />
            {connection ? <Banner kind={connection.kind} text={connection.text} /> : null}
            <Button label="Sign out" icon="log-out-outline" kind="secondary" onPress={signOut} style={styles.button} />
          </View>
        ) : (
          <View>
            <Text style={ui.hint}>
              The address of the PC running server.py. On a phone, start the server with "python server.py --lan" and use the PC's
              Wi-Fi address. Works only on the same network as the PC.
            </Text>
            <Text style={ui.label}>Server address</Text>
            <TextInput
              value={url}
              onChangeText={setUrl}
              placeholder={defaultServerUrl()}
              placeholderTextColor={colors.muted}
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="url"
              style={ui.input}
              accessibilityLabel="Server address"
            />
            <Text style={ui.label}>API key (the contents of .api_key on the PC, if that file exists)</Text>
            <TextInput
              value={key}
              onChangeText={setKey}
              placeholder="none"
              placeholderTextColor={colors.muted}
              autoCapitalize="none"
              autoCorrect={false}
              secureTextEntry
              style={ui.input}
              accessibilityLabel="API key"
            />
            <Button label="Save and test connection" icon="wifi-outline" busy={testing} onPress={saveAndTest} style={styles.button} />
            {connection ? <Banner kind={connection.kind} text={connection.text} /> : null}
          </View>
        )}

        <SectionTitle>Your profile</SectionTitle>
        <Text style={ui.hint}>Everything here is optional. The stylist reads whatever you fill in.</Text>
        {profile ? (
          <View>
            {TEXT_FIELDS.map((field) => (
              <View key={field.key}>
                <Text style={ui.label}>{field.label}</Text>
                <TextInput
                  value={asText(profile[field.key])}
                  onChangeText={(text) => setField(field.key, text)}
                  placeholder={field.placeholder}
                  placeholderTextColor={colors.muted}
                  style={ui.input}
                  accessibilityLabel={field.label}
                />
              </View>
            ))}
            <View style={styles.row}>
              {[
                { key: 'height_cm', label: 'Height (cm)' },
                { key: 'weight_kg', label: 'Weight (kg)' },
              ].map((field) => (
                <View key={field.key} style={styles.cell}>
                  <Text style={ui.label}>{field.label}</Text>
                  <TextInput
                    value={asText(profile[field.key])}
                    onChangeText={(text) => setField(field.key, text.trim() === '' ? null : Number(text) || text)}
                    keyboardType="numeric"
                    style={ui.input}
                    accessibilityLabel={field.label}
                  />
                </View>
              ))}
            </View>
            <View style={styles.row}>
              {SIZE_FIELDS.map((size) => (
                <View key={size} style={styles.cell}>
                  <Text style={ui.label}>Size: {size}</Text>
                  <TextInput
                    value={asText(sizes[size])}
                    onChangeText={(text) => setField('sizes', { ...sizes, [size]: text })}
                    autoCapitalize="characters"
                    style={ui.input}
                    accessibilityLabel={`Size ${size}`}
                  />
                </View>
              ))}
            </View>
            {LIST_FIELDS.map((field) => (
              <View key={field.key}>
                <Text style={ui.label}>{field.label} (comma separated)</Text>
                <TextInput
                  value={asList(profile[field.key])}
                  onChangeText={(text) =>
                    setField(
                      field.key,
                      text.split(',').map((entry) => entry.trimStart()),
                    )
                  }
                  onBlur={() =>
                    setField(
                      field.key,
                      asList(profile[field.key])
                        .split(',')
                        .map((entry) => entry.trim())
                        .filter(Boolean),
                    )
                  }
                  placeholder={field.placeholder}
                  placeholderTextColor={colors.muted}
                  autoCapitalize="none"
                  style={ui.input}
                  accessibilityLabel={field.label}
                />
              </View>
            ))}
            <Button label="Save profile" icon="save-outline" busy={saving} onPress={saveProfile} style={styles.button} />
          </View>
        ) : null}
        {profileStatus ? <Banner kind={profileStatus.kind} text={profileStatus.text} /> : null}
      </ScrollView>
    </Page>
  );
}

const styles = StyleSheet.create({
  content: { padding: space.lg, paddingBottom: space.xl * 2 },
  modes: { flexDirection: 'row', gap: space.sm, marginBottom: space.md },
  account: { color: colors.text, fontSize: 15, fontWeight: '600', marginTop: space.md },
  button: { marginTop: space.lg, marginBottom: space.md },
  row: { flexDirection: 'row', gap: space.md },
  cell: { flex: 1 },
});
