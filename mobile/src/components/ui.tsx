import Ionicons from '@expo/vector-icons/Ionicons';
import type { ComponentProps, ReactNode } from 'react';
import { ActivityIndicator, Alert, Platform, Pressable, StyleSheet, Text, View, type ViewStyle } from 'react-native';

import { colors, MAX_WIDTH, radius, space } from '@/theme';

type IconName = ComponentProps<typeof Ionicons>['name'];

/** Centres content and caps its width so the app also looks right in a browser. */
export function Page({ children, style }: { children: ReactNode; style?: ViewStyle }) {
  return <View style={[styles.page, style]}>{children}</View>;
}

export function Chip({ label, selected, onPress }: { label: string; selected: boolean; onPress: () => void }) {
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityState={{ selected }}
      style={[styles.chip, selected && styles.chipSelected]}>
      <Text style={[styles.chipText, selected && styles.chipTextSelected]}>{label}</Text>
    </Pressable>
  );
}

type ButtonProps = {
  label: string;
  onPress: () => void;
  icon?: IconName;
  kind?: 'primary' | 'secondary' | 'danger';
  busy?: boolean;
  disabled?: boolean;
  style?: ViewStyle;
};

export function Button({ label, onPress, icon, kind = 'primary', busy = false, disabled = false, style }: ButtonProps) {
  const inactive = busy || disabled;
  const foreground = kind === 'primary' ? colors.onAccent : kind === 'danger' ? colors.danger : colors.accent;
  return (
    <Pressable
      onPress={onPress}
      disabled={inactive}
      accessibilityRole="button"
      style={({ pressed }) => [
        styles.button,
        kind === 'primary' && styles.buttonPrimary,
        kind === 'secondary' && styles.buttonSecondary,
        kind === 'danger' && styles.buttonDanger,
        (inactive || pressed) && styles.dimmed,
        style,
      ]}>
      {busy ? <ActivityIndicator color={foreground} /> : icon ? <Ionicons name={icon} size={18} color={foreground} /> : null}
      <Text style={[styles.buttonText, { color: foreground }]}>{label}</Text>
    </Pressable>
  );
}

/** An inline message. Errors can be dismissed. */
export function Banner({ text, kind = 'error', onClose }: { text: string; kind?: 'error' | 'info' | 'good'; onClose?: () => void }) {
  const palette = {
    error: { bg: colors.dangerSoft, fg: colors.danger },
    info: { bg: colors.accentSoft, fg: colors.accent },
    good: { bg: colors.goodSoft, fg: colors.good },
  }[kind];
  return (
    <View style={[styles.banner, { backgroundColor: palette.bg }]}>
      <Text style={[styles.bannerText, { color: palette.fg }]}>{text}</Text>
      {onClose ? (
        <Pressable onPress={onClose} accessibilityLabel="Dismiss" hitSlop={10}>
          <Ionicons name="close" size={18} color={palette.fg} />
        </Pressable>
      ) : null}
    </View>
  );
}

/** A 1-to-5 picker used for formality and warmth. */
export function Scale({ value, onChange, low, high }: { value: number; onChange: (value: number) => void; low: string; high: string }) {
  return (
    <View>
      <View style={styles.scaleRow}>
        {[1, 2, 3, 4, 5].map((step) => (
          <Pressable
            key={step}
            onPress={() => onChange(step)}
            accessibilityRole="button"
            accessibilityState={{ selected: step === value }}
            style={[styles.scaleStep, step === value && styles.chipSelected]}>
            <Text style={[styles.chipText, step === value && styles.chipTextSelected]}>{step}</Text>
          </Pressable>
        ))}
      </View>
      <View style={styles.scaleLabels}>
        <Text style={styles.hint}>{low}</Text>
        <Text style={styles.hint}>{high}</Text>
      </View>
    </View>
  );
}

export function SectionTitle({ children }: { children: ReactNode }) {
  return <Text style={styles.sectionTitle}>{children}</Text>;
}

export function EmptyState({ icon, title, text }: { icon: IconName; title: string; text: string }) {
  return (
    <View style={styles.empty}>
      <Ionicons name={icon} size={40} color={colors.muted} />
      <Text style={styles.emptyTitle}>{title}</Text>
      <Text style={styles.emptyText}>{text}</Text>
    </View>
  );
}

/** Ask before a destructive action. Alert.alert does nothing in a browser, so the web uses window.confirm. */
export function confirmAction(title: string, message: string, actionLabel: string, onConfirm: () => void) {
  if (Platform.OS === 'web') {
    if (typeof window !== 'undefined' && window.confirm(`${title}\n\n${message}`)) onConfirm();
    return;
  }
  Alert.alert(title, message, [
    { text: 'Cancel', style: 'cancel' },
    { text: actionLabel, style: 'destructive', onPress: onConfirm },
  ]);
}

export const styles = StyleSheet.create({
  page: { flex: 1, width: '100%', maxWidth: MAX_WIDTH, alignSelf: 'center' },
  chip: {
    paddingHorizontal: space.md,
    paddingVertical: space.sm,
    borderRadius: radius.pill,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.card,
  },
  chipSelected: { backgroundColor: colors.accent, borderColor: colors.accent },
  chipText: { color: colors.text, fontSize: 14 },
  chipTextSelected: { color: colors.onAccent, fontWeight: '600' },
  button: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: space.sm,
    minHeight: 46,
    paddingHorizontal: space.lg,
    borderRadius: radius.md,
    borderWidth: 1,
  },
  buttonPrimary: { backgroundColor: colors.accent, borderColor: colors.accent },
  buttonSecondary: { backgroundColor: colors.card, borderColor: colors.line },
  buttonDanger: { backgroundColor: colors.card, borderColor: colors.danger },
  buttonText: { fontSize: 15, fontWeight: '600' },
  dimmed: { opacity: 0.6 },
  banner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: space.sm,
    padding: space.md,
    borderRadius: radius.sm,
  },
  bannerText: { flex: 1, fontSize: 14, lineHeight: 19 },
  scaleRow: { flexDirection: 'row', gap: space.sm },
  scaleStep: {
    flex: 1,
    alignItems: 'center',
    paddingVertical: space.sm,
    borderRadius: radius.sm,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.card,
  },
  scaleLabels: { flexDirection: 'row', justifyContent: 'space-between', marginTop: space.xs },
  hint: { color: colors.muted, fontSize: 12, lineHeight: 17 },
  sectionTitle: { color: colors.text, fontSize: 15, fontWeight: '700', marginTop: space.lg, marginBottom: space.sm },
  empty: { alignItems: 'center', padding: space.xl, gap: space.sm },
  emptyTitle: { color: colors.text, fontSize: 17, fontWeight: '700' },
  emptyText: { color: colors.muted, fontSize: 14, lineHeight: 20, textAlign: 'center' },
  input: {
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.line,
    borderRadius: radius.sm,
    paddingHorizontal: space.md,
    paddingVertical: space.md,
    fontSize: 15,
    color: colors.text,
  },
  label: { color: colors.muted, fontSize: 13, marginTop: space.md, marginBottom: space.xs },
});
