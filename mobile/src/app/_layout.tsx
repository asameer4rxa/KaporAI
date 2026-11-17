import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';

import { SettingsProvider, useSettings } from '@/lib/settings';
import { colors } from '@/theme';

/** The app's screens, or the sign-in screen while the cloud connection has nobody signed in. */
function Screens() {
  const { needsSignIn } = useSettings();
  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: colors.card },
        headerTintColor: colors.text,
        contentStyle: { backgroundColor: colors.bg },
      }}>
      <Stack.Protected guard={!needsSignIn}>
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
        <Stack.Screen name="item/[id]" options={{ title: 'Item' }} />
      </Stack.Protected>
      <Stack.Protected guard={needsSignIn}>
        <Stack.Screen name="sign-in" options={{ headerShown: false }} />
      </Stack.Protected>
    </Stack>
  );
}

export default function RootLayout() {
  return (
    <SettingsProvider>
      <StatusBar style="dark" />
      <Screens />
    </SettingsProvider>
  );
}
