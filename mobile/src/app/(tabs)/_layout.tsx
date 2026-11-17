import Ionicons from '@expo/vector-icons/Ionicons';
import { Tabs } from 'expo-router';
import type { ComponentProps } from 'react';

import { colors } from '@/theme';

type IconName = ComponentProps<typeof Ionicons>['name'];

/** A tab icon coloured by focus (the navigator's own colour value is not a plain string in this SDK). */
const icon = (name: IconName) =>
  function TabIcon({ focused, size }: { focused: boolean; size: number }) {
    return <Ionicons name={name} size={size} color={focused ? colors.accent : colors.muted} />;
  };

export default function TabLayout() {
  return (
    <Tabs
      screenOptions={{
        tabBarActiveTintColor: colors.accent,
        tabBarInactiveTintColor: colors.muted,
        headerStyle: { backgroundColor: colors.card },
        headerTintColor: colors.text,
        sceneStyle: { backgroundColor: colors.bg },
      }}>
      <Tabs.Screen name="index" options={{ title: 'Wardrobe', tabBarIcon: icon('shirt-outline') }} />
      <Tabs.Screen name="stylist" options={{ title: 'Stylist', tabBarIcon: icon('sparkles-outline') }} />
      <Tabs.Screen name="outfits" options={{ title: 'Outfits', tabBarIcon: icon('heart-outline') }} />
      <Tabs.Screen name="settings" options={{ title: 'Settings', tabBarIcon: icon('settings-outline') }} />
    </Tabs>
  );
}
