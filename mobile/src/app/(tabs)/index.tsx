import type { ImagePickerAsset } from 'expo-image-picker';
import { router, useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';
import { FlatList, Image, Platform, Pressable, RefreshControl, ScrollView, StyleSheet, Text, useWindowDimensions, View } from 'react-native';

import { Banner, Button, Chip, EmptyState, Page } from '@/components/ui';
import { pickFromLibrary, takePhoto, toUploadable } from '@/lib/photos';
import { useSettings } from '@/lib/settings';
import { CATEGORIES, CATEGORY_LABELS, type Category, type Item } from '@/lib/types';
import { colors, MAX_WIDTH, radius, space } from '@/theme';

export default function WardrobeScreen() {
  const { api, ready } = useSettings();
  const { width } = useWindowDimensions();
  const [items, setItems] = useState<Item[]>([]);
  const [filter, setFilter] = useState<Category | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [progress, setProgress] = useState(''); // e.g. "Adding photo 2 of 5 ..."
  const [notice, setNotice] = useState('');
  const [pcNote, setPcNote] = useState(''); // cloud only: says so when the PC is off

  const load = useCallback(async () => {
    try {
      setItems((await api.items()).items);
      setError('');
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setLoading(false);
    }
    if (api.kind === 'cloud') {
      api
        .status()
        .then((pc) => setPcNote(pc.online ? '' : `${pc.detail} You can still browse, and photos you add are processed when it is back.`))
        .catch(() => undefined);
    } else {
      setPcNote('');
    }
  }, [api]);

  // Reload whenever the tab comes back into view (an item may have been edited or deleted).
  useFocusEffect(
    useCallback(() => {
      if (ready) load();
    }, [ready, load]),
  );

  async function addPhotos(choose: () => Promise<ImagePickerAsset[]>) {
    setError('');
    setNotice('');
    let assets: ImagePickerAsset[];
    try {
      assets = await choose();
    } catch (problem) {
      setError((problem as Error).message);
      return;
    }
    if (assets.length === 0) return;

    let added = 0;
    let duplicates = 0;
    let queued = 0;
    const failures: string[] = [];
    for (let index = 0; index < assets.length; index += 1) {
      setProgress(`Adding photo ${index + 1} of ${assets.length} - about 10 seconds each ...`);
      try {
        const result = await api.upload(await toUploadable(assets[index], index));
        if (result.queued) queued += 1;
        else if (result.duplicate) duplicates += 1;
        else added += 1;
      } catch (problem) {
        failures.push((problem as Error).message);
        if ((problem as { status?: number }).status === 0) break; // unreachable: the rest would fail too
      }
    }
    setProgress('');
    if (failures.length > 0) setError(`${failures.length} photo(s) could not be added: ${failures[0]}`);
    const parts: string[] = [];
    if (added) parts.push(`${added} item(s) added`);
    if (duplicates) parts.push(`${duplicates} already in the wardrobe`);
    if (queued) parts.push(`${queued} waiting for your PC - they will appear once it has processed them`);
    if (parts.length > 0) setNotice(`${parts.join(', ')}.`);
    await load();
  }

  const columns = width >= 700 ? 4 : width >= 480 ? 3 : 2;
  const gridWidth = Math.min(width, MAX_WIDTH) - space.lg * 2;
  const tile = Math.floor((gridWidth - space.md * (columns - 1)) / columns);
  const shown = filter ? items.filter((item) => item.category === filter) : items;
  const busy = progress !== '';

  return (
    <Page>
      <FlatList
        key={columns} // FlatList cannot change numColumns on the fly
        data={shown}
        numColumns={columns}
        keyExtractor={(item) => item.id}
        columnWrapperStyle={styles.row}
        contentContainerStyle={styles.list}
        refreshControl={<RefreshControl refreshing={loading && items.length > 0} onRefresh={load} />}
        ListHeaderComponent={
          <View style={styles.header}>
            <View style={styles.addRow}>
              <Button label="Add photos" icon="images-outline" busy={busy} onPress={() => addPhotos(pickFromLibrary)} style={styles.grow} />
              {Platform.OS !== 'web' ? (
                <Button label="Camera" icon="camera-outline" kind="secondary" disabled={busy} onPress={() => addPhotos(takePhoto)} />
              ) : null}
            </View>
            {pcNote ? <Banner kind="info" text={pcNote} /> : null}
            {progress ? <Banner kind="info" text={progress} /> : null}
            {notice ? <Banner kind="good" text={notice} onClose={() => setNotice('')} /> : null}
            {error ? <Banner text={error} onClose={() => setError('')} /> : null}
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filters}>
              <Chip label={`All (${items.length})`} selected={filter === null} onPress={() => setFilter(null)} />
              {CATEGORIES.map((category) => {
                const count = items.filter((item) => item.category === category).length;
                return count > 0 ? (
                  <Chip
                    key={category}
                    label={`${CATEGORY_LABELS[category]} (${count})`}
                    selected={filter === category}
                    onPress={() => setFilter(category)}
                  />
                ) : null;
              })}
            </ScrollView>
          </View>
        }
        ListEmptyComponent={
          loading ? undefined : (
            <EmptyState
              icon="shirt-outline"
              title="Your wardrobe is empty"
              text="Add photos of your clothes - one garment per photo, laid flat or on a hanger, on a plain background."
            />
          )
        }
        renderItem={({ item }) => (
          <Pressable
            style={[styles.tile, { width: tile }]}
            accessibilityRole="button"
            accessibilityLabel={`${item.primary_color} ${item.subcategory}`}
            onPress={() => router.push({ pathname: '/item/[id]', params: { id: item.id } })}>
            <Image source={{ uri: api.mediaUrl(item.thumb_url ?? item.cutout_url) }} style={{ width: tile - 2, height: tile }} resizeMode="contain" />
            <View style={styles.caption}>
              <Text style={styles.title} numberOfLines={1}>
                {item.subcategory}
              </Text>
              <Text style={styles.subtitle} numberOfLines={1}>
                {item.primary_color}
              </Text>
            </View>
          </Pressable>
        )}
      />
    </Page>
  );
}

const styles = StyleSheet.create({
  list: { padding: space.lg, gap: space.md },
  row: { gap: space.md },
  header: { gap: space.md },
  addRow: { flexDirection: 'row', gap: space.sm },
  grow: { flex: 1 },
  filters: { gap: space.sm, paddingBottom: space.xs },
  tile: {
    backgroundColor: colors.card,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.line,
    overflow: 'hidden',
  },
  caption: { padding: space.sm, borderTopWidth: 1, borderTopColor: colors.line },
  title: { color: colors.text, fontSize: 14, fontWeight: '600' },
  subtitle: { color: colors.muted, fontSize: 12, marginTop: 2 },
});
