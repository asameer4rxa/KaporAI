import type { ImagePickerAsset } from 'expo-image-picker';
import { useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';
import { Image, Platform, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Banner, Button, Chip, confirmAction, EmptyState } from '@/components/ui';
import { pickOne, takePhoto, toUploadable } from '@/lib/photos';
import { useSettings } from '@/lib/settings';
import type { WornEntry } from '@/lib/types';
import { colors, radius, space } from '@/theme';

type View_ = 'days' | 'months';

const asDate = (day: string) => {
  const [year, month, date] = day.split('-').map(Number);
  return new Date(year, month - 1, date);
};
const monthTitle = (day: string) => asDate(day).toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
const dayTitle = (day: string) => asDate(day).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'short' });

/** Groups entries (already newest first) by calendar month, keeping the order. */
function byMonth(entries: WornEntry[]): { month: string; entries: WornEntry[] }[] {
  const groups: { month: string; entries: WornEntry[] }[] = [];
  for (const entry of entries) {
    const month = entry.worn_on.slice(0, 7);
    const last = groups[groups.length - 1];
    if (last && last.month === month) last.entries.push(entry);
    else groups.push({ month, entries: [entry] });
  }
  return groups;
}

/**
 * The "Worn" collection: what you wore on which day, with a photo of you in it when you took one.
 * Two views: Days (a list, one row per day) and Months (a photo grid per month).
 */
export function WornDiary() {
  const { api, ready } = useSettings();
  const [entries, setEntries] = useState<WornEntry[]>([]);
  const [view, setView] = useState<View_>('days');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const load = useCallback(async () => {
    try {
      setEntries(await api.wornDiary());
      setError('');
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setLoading(false);
    }
  }, [api]);

  useFocusEffect(
    useCallback(() => {
      if (ready) load();
    }, [ready, load]),
  );

  async function addPhoto(choose: () => Promise<ImagePickerAsset[]>) {
    setError('');
    setNotice('');
    try {
      const assets = await choose();
      if (assets.length === 0) return;
      setBusy(true);
      const result = await api.addWornPhoto(await toUploadable(assets[0], 0));
      setNotice(result.queued ? 'Photo saved. It appears here once your PC has removed the background.' : "Added to today's entry.");
      await load();
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function removePhoto(entry: WornEntry) {
    confirmAction('Remove this photo?', 'The record of what you wore that day is kept.', 'Remove', async () => {
      try {
        await api.deleteWornPhoto(entry.id);
        setEntries((current) => current.filter((other) => other.id !== entry.id));
      } catch (problem) {
        setError((problem as Error).message);
      }
    });
  }

  const groups = byMonth(entries);

  return (
    <ScrollView contentContainerStyle={styles.content} refreshControl={<RefreshControl refreshing={loading && entries.length > 0} onRefresh={load} />}>
      <View style={styles.addRow}>
        <Button label="Add a photo of today's outfit" icon="image-outline" busy={busy} onPress={() => addPhoto(pickOne)} style={styles.grow} />
        {Platform.OS !== 'web' ? (
          <Button label="Camera" icon="camera-outline" kind="secondary" disabled={busy} onPress={() => addPhoto(takePhoto)} />
        ) : null}
      </View>
      <View style={styles.views}>
        <Chip label="Days" selected={view === 'days'} onPress={() => setView('days')} />
        <Chip label="Months" selected={view === 'months'} onPress={() => setView('months')} />
      </View>
      {notice ? <Banner kind="good" text={notice} onClose={() => setNotice('')} /> : null}
      {error ? <Banner text={error} onClose={() => setError('')} /> : null}

      {!loading && entries.length === 0 ? (
        <EmptyState
          icon="calendar-outline"
          title="Nothing worn yet"
          text='Tap "I wore this" on an outfit, or add a photo of what you are wearing today.'
        />
      ) : null}

      {groups.map((group) => (
        <View key={group.month} style={styles.month}>
          <Text style={styles.monthTitle}>
            {monthTitle(group.entries[0].worn_on)}
            <Text style={styles.count}>  {group.entries.length} {group.entries.length === 1 ? 'day logged' : 'entries'}</Text>
          </Text>

          {view === 'days' ? (
            group.entries.map((entry) => (
              <View key={entry.id} style={styles.day}>
                {entry.photo_url ? (
                  <Image source={{ uri: api.mediaUrl(entry.photo_url) }} style={styles.dayPhoto} resizeMode="contain" />
                ) : (
                  <View style={[styles.dayPhoto, styles.noPhoto]}>
                    <Text style={styles.noPhotoText}>{entry.has_photo ? 'Processing ...' : 'No photo'}</Text>
                  </View>
                )}
                <View style={styles.dayText}>
                  <Text style={styles.dayTitle}>{dayTitle(entry.worn_on)}</Text>
                  {entry.outfit ? (
                    <>
                      <Text style={styles.outfitName}>{entry.outfit.name}</Text>
                      <Text style={styles.pieces} numberOfLines={3}>
                        {entry.outfit.items.map((item) => `${item.primary_color} ${item.subcategory}`).join(' · ')}
                      </Text>
                    </>
                  ) : (
                    <Text style={styles.pieces}>Your own outfit</Text>
                  )}
                  {entry.has_photo ? (
                    <Pressable onPress={() => removePhoto(entry)} accessibilityRole="button" hitSlop={8}>
                      <Text style={styles.remove}>Remove photo</Text>
                    </Pressable>
                  ) : null}
                </View>
              </View>
            ))
          ) : (
            <View style={styles.grid}>
              {group.entries.map((entry) => (
                <View key={entry.id} style={styles.tile}>
                  {entry.photo_url ? (
                    <Image source={{ uri: api.mediaUrl(entry.photo_url) }} style={styles.tilePhoto} resizeMode="contain" />
                  ) : (
                    <View style={[styles.tilePhoto, styles.noPhoto]}>
                      <Text style={styles.noPhotoText} numberOfLines={3}>
                        {entry.outfit?.name ?? (entry.has_photo ? 'Processing ...' : 'No photo')}
                      </Text>
                    </View>
                  )}
                  <Text style={styles.tileDay}>{asDate(entry.worn_on).getDate()}</Text>
                </View>
              ))}
            </View>
          )}
        </View>
      ))}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  content: { padding: space.lg, gap: space.md, paddingBottom: space.xl * 2 },
  addRow: { flexDirection: 'row', gap: space.sm },
  grow: { flex: 1 },
  views: { flexDirection: 'row', gap: space.sm },
  month: { gap: space.sm },
  monthTitle: { color: colors.text, fontSize: 17, fontWeight: '700', marginTop: space.sm },
  count: { color: colors.muted, fontSize: 13, fontWeight: '400' },
  day: {
    flexDirection: 'row',
    gap: space.md,
    backgroundColor: colors.card,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.line,
    padding: space.md,
  },
  dayPhoto: { width: 92, height: 132, borderRadius: radius.sm, backgroundColor: colors.bg },
  dayText: { flex: 1, gap: space.xs },
  dayTitle: { color: colors.text, fontSize: 15, fontWeight: '700' },
  outfitName: { color: colors.text, fontSize: 14 },
  pieces: { color: colors.muted, fontSize: 13, lineHeight: 18 },
  remove: { color: colors.danger, fontSize: 13, marginTop: space.xs },
  noPhoto: { alignItems: 'center', justifyContent: 'center', padding: space.xs },
  noPhotoText: { color: colors.muted, fontSize: 12, textAlign: 'center' },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm },
  tile: { width: 100 },
  tilePhoto: {
    width: 100,
    height: 140,
    borderRadius: radius.sm,
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.line,
  },
  tileDay: { color: colors.muted, fontSize: 12, textAlign: 'center', marginTop: 2 },
});
