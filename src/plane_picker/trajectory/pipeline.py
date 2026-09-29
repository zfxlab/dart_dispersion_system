from dataclasses import asdict, dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class TrajectoryConfig:
    """Parameters for conservative background subtraction and enhancement."""

    noise_multiplier: float = 5.0
    dark_weight: float = 0.7
    minimum_threshold_8bit: float = 3.0
    minimum_area_px: int = 20
    enhancement_gain: float = 4.0

    def __post_init__(self):
        values = (
            self.noise_multiplier, self.dark_weight,
            self.minimum_threshold_8bit, self.enhancement_gain,
        )
        if not all(np.isfinite(value) and value > 0 for value in values):
            raise ValueError("轨迹处理参数必须为有限正数")
        if type(self.minimum_area_px) is not int or self.minimum_area_px < 1:
            raise ValueError("minimum_area_px 必须是正整数")

    def to_dict(self):
        return asdict(self)


def _color(image):
    array = np.asarray(image)
    if array.ndim == 2:
        array = cv2.cvtColor(array, cv2.COLOR_GRAY2BGR)
    elif array.ndim == 3 and array.shape[2] == 4:
        array = array[:, :, :3]
    if array.ndim != 3 or array.shape[2] != 3:
        raise ValueError("轨迹处理只支持灰度、BGR 或 BGRA 图像")
    if array.dtype == np.uint8:
        scale = 255.0
    elif array.dtype == np.uint16:
        scale = 65535.0
    else:
        raise ValueError("轨迹处理只支持 8 位或 16 位整数图像")
    return array.astype(np.float32) / scale


def _match_background(background, foreground):
    bg_gray = cv2.cvtColor(background, cv2.COLOR_BGR2GRAY)
    fg_gray = cv2.cvtColor(foreground, cv2.COLOR_BGR2GRAY)
    usable = (bg_gray > 0.01) & (bg_gray < 0.99) & (fg_gray < 0.99)
    if usable.sum() < 100:
        return background, 1.0, 0.0
    bg_low, bg_high = np.percentile(bg_gray[usable], [10, 90])
    fg_low, fg_high = np.percentile(fg_gray[usable], [10, 90])
    span = bg_high - bg_low
    gain = 1.0 if span < 1e-6 else float((fg_high - fg_low) / span)
    gain = float(np.clip(gain, 0.5, 2.0))
    offset = float(np.clip(fg_low - gain * bg_low, -0.25, 0.25))
    return np.clip(background * gain + offset, 0.0, 1.0), gain, offset


def enhance_trajectory(foreground, backgrounds, config=None):
    """Return display outputs without changing source pixel geometry."""
    config = config or TrajectoryConfig()
    fg = _color(foreground)
    background_list = [_color(image) for image in backgrounds]
    if not background_list:
        raise ValueError("至少需要一张 _background 背景图片")
    if any(image.shape != fg.shape for image in background_list):
        raise ValueError("背景图片与长曝光图片尺寸不一致")

    background = np.median(np.stack(background_list), axis=0).astype(np.float32)
    adjusted, gain, offset = _match_background(background, fg)
    delta = fg - adjusted
    gray_delta = cv2.cvtColor(delta, cv2.COLOR_BGR2GRAY)
    center = float(np.median(gray_delta))
    sigma = float(1.4826 * np.median(np.abs(gray_delta - center)))
    bright = np.max(delta, axis=2)
    dark = np.max(-delta, axis=2) * config.dark_weight
    score = np.maximum(bright, dark)
    score = cv2.GaussianBlur(score, (3, 3), 0)
    threshold = max(
        config.minimum_threshold_8bit / 255.0,
        config.noise_multiplier * sigma,
    )
    initial = (score >= threshold).astype(np.uint8)
    closed = cv2.morphologyEx(
        initial, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
    )
    count, labels, stats, _ = cv2.connectedComponentsWithStats(closed, 8)
    mask = np.zeros_like(closed)
    kept = 0
    for label in range(1, count):
        if int(stats[label, cv2.CC_STAT_AREA]) >= config.minimum_area_px:
            mask[labels == label] = 255
            kept += 1

    soft_mask = cv2.GaussianBlur(mask.astype(np.float32) / 255.0, (5, 5), 0)
    enhanced = adjusted + (
        config.enhancement_gain * delta * soft_mask[:, :, None]
    )
    overlay = np.clip(enhanced * 255.0, 0, 255).astype(np.uint8)

    ceiling = max(threshold * 4.0, float(np.percentile(score, 99.5)), 1e-6)
    normalized = np.clip(score / ceiling, 0.0, 1.0)
    heat = cv2.applyColorMap(
        (normalized * 255.0).astype(np.uint8), cv2.COLORMAP_TURBO,
    )
    metrics = {
        "background_count": len(background_list),
        "photometric_gain": gain,
        "photometric_offset": offset,
        "noise_sigma_8bit": sigma * 255.0,
        "threshold_8bit": threshold * 255.0,
        "changed_pixel_ratio": float(np.count_nonzero(mask) / mask.size),
        "component_count": kept,
    }
    return {
        "overlay": overlay,
        "difference": heat,
        "mask": mask,
        "metrics": metrics,
    }
