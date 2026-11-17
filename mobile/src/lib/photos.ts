import { manipulateAsync, SaveFormat, type Action } from 'expo-image-manipulator';
import * as ImagePicker from 'expo-image-picker';

import type { Uploadable } from './types';

const MAX_SIDE = 2000; // the server works at 2048 px anyway; smaller uploads are faster over Wi-Fi

/** Let the user choose one or more clothing photos from their library. */
export async function pickFromLibrary(): Promise<ImagePicker.ImagePickerAsset[]> {
  const result = await ImagePicker.launchImageLibraryAsync({
    mediaTypes: ['images'],
    allowsMultipleSelection: true,
    selectionLimit: 20,
    quality: 1,
  });
  return result.canceled ? [] : result.assets;
}

/** Let the user choose a single photo (on an iPhone this also offers the camera). */
export async function pickOne(): Promise<ImagePicker.ImagePickerAsset[]> {
  const result = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['images'], allowsMultipleSelection: false, quality: 1 });
  return result.canceled ? [] : result.assets.slice(0, 1);
}

/** Take one photo with the camera (phones only). */
export async function takePhoto(): Promise<ImagePicker.ImagePickerAsset[]> {
  const permission = await ImagePicker.requestCameraPermissionsAsync();
  if (!permission.granted) {
    throw new Error('Camera permission was not granted. Allow it in your phone settings, or pick from the library.');
  }
  const result = await ImagePicker.launchCameraAsync({ mediaTypes: ['images'], quality: 1 });
  return result.canceled ? [] : result.assets;
}

/**
 * Re-encode a picked photo as a JPEG no larger than 2000 px.
 * This also turns iPhone HEIC photos into something the server can read.
 */
export async function toUploadable(asset: ImagePicker.ImagePickerAsset, index: number): Promise<Uploadable> {
  const actions: Action[] = [];
  if (Math.max(asset.width, asset.height) > MAX_SIDE) {
    actions.push({ resize: asset.width >= asset.height ? { width: MAX_SIDE } : { height: MAX_SIDE } });
  }
  const result = await manipulateAsync(asset.uri, actions, { compress: 0.9, format: SaveFormat.JPEG });
  const stem = (asset.fileName ?? `photo_${Date.now()}_${index}`).replace(/\.[^.]+$/, '');
  return { uri: result.uri, name: `${stem}.jpg` };
}
