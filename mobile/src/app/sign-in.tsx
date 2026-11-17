import { useState } from 'react';
import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet, Text, TextInput } from 'react-native';

import { Banner, Button, Page, styles as ui } from '@/components/ui';
import { useSettings } from '@/lib/settings';
import { colors, space } from '@/theme';

/** Shown instead of the app while the cloud connection has nobody signed in. */
export default function SignInScreen() {
  const { signIn, setMode } = useSettings();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function submit() {
    if (!email.trim() || !password) {
      setError('Enter the email and password of your FashionAI login.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await signIn(email, password); // on success the app switches to the wardrobe by itself
    } catch (problem) {
      setError((problem as Error).message);
      setBusy(false);
    }
  }

  return (
    <Page>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.flex}>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <Text style={styles.title}>FashionAI</Text>
          <Text style={styles.lead}>Sign in to open your wardrobe from anywhere.</Text>

          <Text style={ui.label}>Email</Text>
          <TextInput
            value={email}
            onChangeText={setEmail}
            autoCapitalize="none"
            autoCorrect={false}
            autoComplete="email"
            keyboardType="email-address"
            style={ui.input}
            accessibilityLabel="Email"
          />
          <Text style={ui.label}>Password</Text>
          <TextInput
            value={password}
            onChangeText={setPassword}
            secureTextEntry
            autoCapitalize="none"
            autoComplete="current-password"
            onSubmitEditing={submit}
            style={ui.input}
            accessibilityLabel="Password"
          />
          <Button label="Sign in" icon="log-in-outline" busy={busy} onPress={submit} style={styles.button} />
          {error ? <Banner text={error} onClose={() => setError('')} /> : null}

          <Text style={[ui.hint, styles.note]}>
            This is the app login created in your Supabase project (Authentication, Users), not your Supabase account.
          </Text>
          <Button
            label="Connect straight to the PC instead"
            icon="desktop-outline"
            kind="secondary"
            onPress={() => setMode('direct')}
            style={styles.button}
          />
          <Text style={ui.hint}>Works only on the same Wi-Fi as the PC, without any cloud.</Text>
        </ScrollView>
      </KeyboardAvoidingView>
    </Page>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  content: { padding: space.xl, paddingTop: space.xl * 3 },
  title: { color: colors.text, fontSize: 30, fontWeight: '800' },
  lead: { color: colors.muted, fontSize: 15, marginTop: space.xs, marginBottom: space.lg },
  button: { marginTop: space.lg, marginBottom: space.sm },
  note: { marginTop: space.lg },
});
