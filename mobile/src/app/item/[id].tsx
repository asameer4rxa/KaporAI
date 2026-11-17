import { router, Stack, useLocalSearchParams } from 'expo-router';
import { useEffect, useState } from 'react';
import { ActivityIndicator, Image, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';

import { Banner, Button, Chip, confirmAction, Page, Scale, SectionTitle, styles as ui } from '@/components/ui';
import { useSettings } from '@/lib/settings';
import { CATEGORIES, CATEGORY_LABELS, FITS, PATTERNS, SEASONS, type Item, type ItemPatch } from '@/lib/types';
import { colors, radius, space } from '@/theme';

type Status = { kind: 'good' | 'error'; text: string };

/** One wardrobe item: its cutout, its tags (editable), and retag / delete. */
export default function ItemScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { api, ready } = useSettings();
  const [item, setItem] = useState<Item | null>(null);
  const [draft, setDraft] = useState<Item | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState<'' | 'save' | 'retag' | 'delete'>('');

  useEffect(() => {
    if (!ready || !id) return;
    api
      .item(id)
      .then((loaded) => {
        setItem(loaded);
        setDraft(loaded);
      })
      .catch((problem: Error) => setStatus({ kind: 'error', text: problem.message }));
  }, [api, ready, id]);

  function change<K extends keyof Item>(key: K, value: Item[K]) {
    setDraft((current) => (current ? { ...current, [key]: value } : current));
    setStatus(null);
  }

  /** Only the fields that differ from what the server has. */
  function pendingChanges(): ItemPatch {
    if (!item || !draft) return {};
    const patch: Record<string, unknown> = {};
    const keys: (keyof ItemPatch)[] = [
      'category', 'subcategory', 'primary_color', 'pattern', 'material', 'fit', 'formality', 'warmth', 'seasons', 'description',
    ];
    for (const key of keys) {
      if (JSON.stringify(draft[key]) !== JSON.stringify(item[key])) patch[key] = draft[key];
    }
    return patch as ItemPatch;
  }

  async function run(action: 'save' | 'retag', work: () => Promise<Item>, done: string) {
    setBusy(action);
    setStatus(null);
    try {
      const updated = await work();
      setItem(updated);
      setDraft(updated);
      setStatus({ kind: 'good', text: done });
    } catch (problem) {
      setStatus({ kind: 'error', text: (problem as Error).message });
    } finally {
      setBusy('');
    }
  }

  function remove() {
    if (!item) return;
    confirmAction(
      'Remove this item?',
      'It leaves your wardrobe. The original photo is kept on the PC in wardrobe_removed.',
      'Remove',
      async () => {
        setBusy('delete');
        try {
          await api.deleteItem(item.id);
          router.back();
        } catch (problem) {
          setStatus({ kind: 'error', text: (problem as Error).message });
          setBusy('');
        }
      },
    );
  }

  if (!draft || !item) {
    return (
      <Page style={styles.centered}>
        <Stack.Screen options={{ title: 'Item' }} />
        {status ? <Banner text={status.text} /> : <ActivityIndicator color={colors.accent} />}
      </Page>
    );
  }

  const changes = pendingChanges();
  const dirty = Object.keys(changes).length > 0;

  return (
    <Page>
      <Stack.Screen options={{ title: item.subcategory }} />
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <Image source={{ uri: api.mediaUrl(item.cutout_url) }} style={styles.cutout} resizeMode="contain" />
        {!item.bg_removed ? <Banner kind="info" text="The background could not be removed from this photo." /> : null}
        {item.worn_photo ? <Banner kind="info" text="Tagged from a photo of someone wearing it." /> : null}

        <Text style={ui.label}>Description</Text>
        <TextInput
          value={draft.description}
          onChangeText={(text) => change('description', text)}
          multiline
          style={[ui.input, styles.multiline]}
          accessibilityLabel="Description"
        />

        <SectionTitle>Category</SectionTitle>
        <View style={styles.wrap}>
          {CATEGORIES.map((category) => (
            <Chip
              key={category}
              label={CATEGORY_LABELS[category]}
              selected={draft.category === category}
              onPress={() => change('category', category)}
            />
          ))}
        </View>

        <View style={styles.row}>
          <View style={styles.cell}>
            <Text style={ui.label}>Type</Text>
            <TextInput
              value={draft.subcategory}
              onChangeText={(text) => change('subcategory', text)}
              autoCapitalize="none"
              style={ui.input}
              accessibilityLabel="Type"
            />
          </View>
          <View style={styles.cell}>
            <Text style={ui.label}>Main colour</Text>
            <TextInput
              value={draft.primary_color}
              onChangeText={(text) => change('primary_color', text)}
              autoCapitalize="none"
              style={ui.input}
              accessibilityLabel="Main colour"
            />
          </View>
        </View>
        <Text style={ui.label}>Material</Text>
        <TextInput
          value={draft.material}
          onChangeText={(text) => change('material', text)}
          autoCapitalize="none"
          style={ui.input}
          accessibilityLabel="Material"
        />

        <SectionTitle>Pattern</SectionTitle>
        <View style={styles.wrap}>
          {PATTERNS.map((pattern) => (
            <Chip key={pattern} label={pattern} selected={draft.pattern === pattern} onPress={() => change('pattern', pattern)} />
          ))}
        </View>

        <SectionTitle>Fit</SectionTitle>
        <View style={styles.wrap}>
          {FITS.map((fit) => (
            <Chip key={fit} label={fit} selected={draft.fit === fit} onPress={() => change('fit', fit)} />
          ))}
        </View>

        <SectionTitle>Formality</SectionTitle>
        <Scale value={draft.formality} onChange={(value) => change('formality', value)} low="gym / lounge" high="black tie" />

        <SectionTitle>Warmth</SectionTitle>
        <Scale value={draft.warmth} onChange={(value) => change('warmth', value)} low="hot weather" high="deep winter" />

        <SectionTitle>Seasons</SectionTitle>
        <View style={styles.wrap}>
          {SEASONS.map((season) => {
            const on = draft.seasons.includes(season);
            return (
              <Chip
                key={season}
                label={season}
                selected={on}
                onPress={() => {
                  const next = on ? draft.seasons.filter((entry) => entry !== season) : SEASONS.filter((entry) => entry === season || draft.seasons.includes(entry));
                  if (next.length > 0) change('seasons', next); // an item always suits at least one season
                }}
              />
            );
          })}
        </View>

        {status ? (
          <View style={styles.status}>
            <Banner kind={status.kind} text={status.text} />
          </View>
        ) : null}

        <Button
          label={dirty ? 'Save changes' : 'No changes to save'}
          icon="save-outline"
          disabled={!dirty || busy !== ''}
          busy={busy === 'save'}
          onPress={() => run('save', () => api.editItem(item.id, changes), api.kind === 'cloud' ? 'Saved. Your PC picks the change up on its next sync.' : 'Saved.')}
          style={styles.action}
        />
        <Button
          label="Tag again with AI"
          icon="refresh-outline"
          kind="secondary"
          disabled={busy !== ''}
          busy={busy === 'retag'}
          onPress={() => run('retag', () => api.retagItem(item.id), 'Tagged again. Your manual changes were replaced.')}
          style={styles.action}
        />
        <Button
          label="Remove from wardrobe"
          icon="trash-outline"
          kind="danger"
          disabled={busy !== ''}
          busy={busy === 'delete'}
          onPress={remove}
          style={styles.action}
        />
        <Text style={[ui.hint, styles.footer]}>
          {item.wear_count ? `Worn ${item.wear_count} time${item.wear_count === 1 ? '' : 's'}, last on ${item.last_worn}` : 'Not worn yet'}
          {'\n'}Tagged by {item.tagger_model} · added {item.created_at.slice(0, 10)}
        </Text>
      </ScrollView>
    </Page>
  );
}

const styles = StyleSheet.create({
  centered: { justifyContent: 'center', padding: space.lg },
  content: { padding: space.lg, paddingBottom: space.xl * 2 },
  cutout: {
    width: '100%',
    height: 300,
    backgroundColor: colors.card,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.line,
    marginBottom: space.sm,
  },
  multiline: { minHeight: 64, textAlignVertical: 'top' },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm },
  row: { flexDirection: 'row', gap: space.md },
  cell: { flex: 1 },
  status: { marginTop: space.lg },
  action: { marginTop: space.md },
  footer: { marginTop: space.lg, textAlign: 'center' },
});
