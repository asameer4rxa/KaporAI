import Ionicons from '@expo/vector-icons/Ionicons';
import { router } from 'expo-router';
import { useState } from 'react';
import { Image, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { useSettings } from '@/lib/settings';
import type { Outfit } from '@/lib/types';
import { colors, radius, space } from '@/theme';

type Props = {
  outfit: Outfit;
  /** Called after the like / dislike was saved on the server. */
  onFeedback?: (liked: boolean | null) => void;
};

/** One suggested outfit: its pieces side by side, the reasoning, and like / dislike. */
export function OutfitCard({ outfit, onFeedback }: Props) {
  const { api } = useSettings();
  const [liked, setLiked] = useState<boolean | null>(outfit.liked);
  const [saving, setSaving] = useState(false);
  const [wornOn, setWornOn] = useState<string | null>(outfit.worn_on ?? null);
  const [problem, setProblem] = useState('');

  async function wore() {
    if (outfit.id === null || saving) return;
    setSaving(true);
    setProblem('');
    try {
      setWornOn(await api.wearOutfit(outfit.id));
    } catch (failure) {
      setProblem((failure as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function rate(value: boolean) {
    if (outfit.id === null || saving) return;
    const next = liked === value ? null : value; // tapping the active thumb clears it
    setSaving(true);
    try {
      await api.feedback(outfit.id, next);
      setLiked(next);
      onFeedback?.(next);
    } catch {
      // leave the thumbs as they were; the next tap retries
    } finally {
      setSaving(false);
    }
  }

  return (
    <View style={styles.card}>
      <Text style={styles.name}>{outfit.name}</Text>
      {outfit.occasion ? (
        <Text style={styles.meta}>
          {outfit.occasion}
          {outfit.created_at ? `  ·  ${outfit.created_at.slice(0, 10)}` : ''}
        </Text>
      ) : null}

      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.pieces}>
        {outfit.items.map((item) => (
          <Pressable
            key={item.id}
            style={styles.piece}
            accessibilityRole="button"
            accessibilityLabel={`${item.primary_color} ${item.subcategory}`}
            onPress={() => router.push({ pathname: '/item/[id]', params: { id: item.id } })}>
            <Image source={{ uri: api.mediaUrl(item.thumb_url ?? item.cutout_url) }} style={styles.pieceImage} resizeMode="contain" />
            <Text style={styles.pieceLabel} numberOfLines={2}>
              {item.subcategory}
            </Text>
          </Pressable>
        ))}
      </ScrollView>
      {outfit.items.length === 0 ? <Text style={styles.meta}>The items of this outfit were removed from the wardrobe.</Text> : null}

      {outfit.missing && outfit.missing.length > 0 ? (
        <Text style={styles.warning}>Incomplete: no {outfit.missing.join(' and no ')} chosen.</Text>
      ) : null}
      {outfit.why ? <Text style={styles.body}>{outfit.why}</Text> : null}
      {outfit.tips ? <Text style={styles.tips}>Tip: {outfit.tips}</Text> : null}

      {outfit.id !== null ? (
        <View style={styles.actions}>
          <Pressable
            onPress={() => rate(true)}
            accessibilityRole="button"
            accessibilityLabel="Like this outfit"
            accessibilityState={{ selected: liked === true }}
            style={[styles.thumb, liked === true && styles.thumbLiked]}>
            <Ionicons name={liked === true ? 'heart' : 'heart-outline'} size={18} color={liked === true ? colors.good : colors.muted} />
            <Text style={[styles.thumbText, liked === true && { color: colors.good }]}>Like</Text>
          </Pressable>
          <Pressable
            onPress={() => rate(false)}
            accessibilityRole="button"
            accessibilityLabel="Dislike this outfit"
            accessibilityState={{ selected: liked === false }}
            style={[styles.thumb, liked === false && styles.thumbDisliked]}>
            <Ionicons
              name={liked === false ? 'thumbs-down' : 'thumbs-down-outline'}
              size={18}
              color={liked === false ? colors.danger : colors.muted}
            />
            <Text style={[styles.thumbText, liked === false && { color: colors.danger }]}>Not for me</Text>
          </Pressable>
          <Pressable
            onPress={wore}
            accessibilityRole="button"
            accessibilityLabel="I wore this outfit today"
            style={[styles.thumb, wornOn !== null && styles.thumbWorn]}>
            <Ionicons name={wornOn ? 'checkmark-circle' : 'checkmark-circle-outline'} size={18} color={wornOn ? colors.accent : colors.muted} />
            <Text style={[styles.thumbText, wornOn !== null && { color: colors.accent }]}>{wornOn ? `Worn ${wornOn}` : 'I wore this'}</Text>
          </Pressable>
        </View>
      ) : null}
      {problem ? <Text style={styles.warning}>{problem}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.card,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.line,
    padding: space.lg,
    gap: space.sm,
  },
  name: { color: colors.text, fontSize: 17, fontWeight: '700' },
  meta: { color: colors.muted, fontSize: 13 },
  pieces: { gap: space.sm, paddingVertical: space.xs },
  piece: { width: 108 },
  pieceImage: { width: 108, height: 124, backgroundColor: colors.bg, borderRadius: radius.sm },
  pieceLabel: { color: colors.muted, fontSize: 12, marginTop: space.xs, textAlign: 'center' },
  body: { color: colors.text, fontSize: 14, lineHeight: 20 },
  tips: { color: colors.muted, fontSize: 14, lineHeight: 20 },
  warning: { color: colors.danger, fontSize: 13 },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm, marginTop: space.xs },
  thumbWorn: { backgroundColor: colors.accentSoft, borderColor: colors.accent },
  thumb: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: space.xs,
    paddingHorizontal: space.md,
    paddingVertical: space.sm,
    borderRadius: radius.pill,
    borderWidth: 1,
    borderColor: colors.line,
  },
  thumbLiked: { backgroundColor: colors.goodSoft, borderColor: colors.good },
  thumbDisliked: { backgroundColor: colors.dangerSoft, borderColor: colors.danger },
  thumbText: { color: colors.muted, fontSize: 13, fontWeight: '600' },
});
