# AprilTag 平面点选定位工具

桌面工具，目标平台为 Ubuntu 24.04 / Python 3.12。程序使用 AprilTag 标定照片建立**去畸变像素 → Z=0 平面毫米坐标**映射；随后按“一级文件夹名 = 飞镖 ID、纯数字图片名 = 次数”批量选点，并汇总查看每个飞镖的散布结果。

本项目不包含相机驱动或实时取流；图像应由采集工具保存后再载入。本项目不计算双目外参和三维轨迹；空中点通过本工具得到的是平面映射结果，不能当作真实三维位置。

## Ubuntu 24.04 安装与启动

在本项目根目录运行：

```bash
sudo apt update
sudo apt install libgl1 libegl1 libglib2.0-0 \
    libxkbcommon-x11-0 libxcb-cursor0 libxcb-xinerama0 fonts-noto-cjk
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.lock
uv pip install --python .venv/bin/python --no-deps -e .
uv run --no-sync plane-picker
```

应用统一使用一份配置：

```bash
uv run --no-sync plane-picker --config config/app_config.yaml
```

`requirements.lock` 由 uv 生成并锁定本次验证的运行与测试依赖；上面的两条
`uv pip install` 命令先安装锁定版本，再以 editable 模式安装本项目。

AprilTag 默认后端为 `pupil-apriltags`，绑定 AprilTag 3 的 C 检测库，支持 `tagStandard41h12`。Ubuntu x86_64 的 wheel 包含本地库，通常不需要单独安装 `libapriltag`。如果目标架构没有可用 wheel，需要安装 `build-essential cmake` 后从源码构建该绑定。后端缺失或 family 不支持时会明确报错。

## 加载外部标定的相机内参

相机内参由外部程序完成标定，本项目不提供内参标定工具。内参 YAML 格式参考
`config/calibration/00DA1923282.yaml`，然后通过界面“导入相机内参”复制到当前项目的
`_calibration/camera_intrinsics.yaml`。程序保留
`CameraModel` 和去畸变计算，因为平面映射仍需要 K、D。

`00DA1923282.yaml` 中的参数是按镜头、物距和像元尺寸计算的暂定示例，并假设零畸变，
只用于展示格式和联调；正式测量应替换为该相机在实际分辨率、镜头和对焦状态下的实测标定。

需要填写：

- `image_width/image_height`：内参对应的原始图像尺寸，必须与当前图像一致。
- `camera_matrix.data`：按行展开的 3×3 K，fx/fy/cx/cy 单位为像素。
- `distortion_model`：支持 `plumb_bob` 和 `rational_polynomial`，不接受鱼眼 `equidistant`。
- `distortion_coefficients.data`：`plumb_bob` 为 4/5 项，常用顺序是
  `[k1, k2, p1, p2, k3]`；`rational_polynomial` 为 8 项。
- `rectification_matrix` 和 `projection_matrix`：填写 3×3 R 和 3×4 P；普通单目内参可分别使用单位矩阵和由 K 扩展的投影矩阵。

程序会根据 K、D 和分辨率生成一致的内参版本哈希。
输入必须使用原始图像，不要把已经去畸变的图片与原始畸变参数组合使用。图像裁剪、缩放、
对焦或相机设置改变后，必须重新生成匹配的内参。

## 5×5、1 m×1 m 图案的配置

当前实物板的完整布局保存在 `config/tag_layout.yaml`。识别到的 family 是 `tag36h11`，
ID 按照片从上到下为 `7–11、22–26、37–41、52–56、67–71`。Tag 中心继续使用既有
平面坐标系；照片中的所有 Tag 相对编码正向旋转了 180°，因此所有条目的
`rotation_deg` 都是 `180`。

实测 Tag 检测边长为 168 mm，相邻 Tag 黑色边缘间距为 40 mm，因此中心距为 208 mm。
5 个 Tag 加 4 个间隙正好覆盖 1000 mm：`5×168 + 4×40 = 1000`。以阵列左下边缘为
原点，各行列中心坐标为 `84、292、500、708、916 mm`。

布局以“所有 Tag 编码正向朝上时”的板左下角为原点。等间距阵列中心为（r 从下往上计数，
c 从左往右计数，r/c 为 0–4）：

```text
X(r,c) = x0 + c * pitch_x_mm
Y(r,c) = y0 + r * pitch_y_mm
```

这里 `x0=y0=84 mm`、`pitch_x=pitch_y=208 mm`。由于布局方向与当前照片相反，照片右上角
的 ID 11 是布局左下角第一个 Tag；照片左下角的 ID 67 是布局右上角最后一个 Tag。

有效点击区域继续使用保留内点的凸包，不能直接扩大成整个 1000×1000 mm 外框；外围留白可能不在标定覆盖范围内。更换布局后需重新检测和计算平面标定，旧 H/布局哈希不能沿用。默认网格已为 100 mm，适合该尺寸；RANSAC 阈值由实际角点误差决定，不随 Tag 数量自动放大。

仓库的四 Tag 演示仍是独立的软件验证数据，不代表这张 25 Tag 实物板。确认实物参数后再生成正式布局，避免用猜测的尺寸或 ID 产生错误坐标。

## 坐标系、布局和角点顺序

所有物理坐标内部统一为 mm。平面是 Z=0，+X 向右、+Y 向上；平面视图用 `(X, -Y)` 绘制，以适应屏幕 Y 向下的约定。

`config/tag_layout.yaml` 是当前 5×5 实物板布局。`center_mm` 是 Tag **中心**，不是边角；
`rotation_deg` 是从未旋转印刷方向开始，在物理 +X/+Y 平面内逆时针旋转的角度。
`origin_description` 是说明性元数据，不会额外平移坐标。

检测器的角点顺序固定为未旋转 Tag 的 **左下、右下、右上、左上**。这个顺序由 Tag 编码方向确定，不能按图像上的 x/y 排序。令 `s = tag_size_mm / 2`，对应局部平面坐标为：

```text
0: (-s, -s)    1: (+s, -s)    2: (+s, +s)    3: (-s, +s)
plane_corner = rotation_CCW(rotation_deg) @ local_corner + center_mm
```

该约定与 [AprilTag 3 的 det->p 构造](https://github.com/AprilRobotics/apriltag/blob/master/apriltag.c) 对齐，并由真实 C 检测器在 0°、90°、180°、270° 的测试验证。绑定保留原顺序，不重新排序。将来替换 OpenCV 后端时，必须在适配器中显式转换角点顺序。

`tag_size_mm` 是**检测四边形的边长**，不是打印纸宽度或包含全部白边的图片宽度。`tagStandard41h12` 存在检测边界以外的编码/边界区域；请按所用 Tag 的检测角点间距测量，参见 [AprilTag 官方尺寸定义](https://github.com/AprilRobotics/apriltag#pose-estimation)。打印必须保持比例并保留完整外部图案和留白。

配置加载会拒绝非 mm 单位、非正尺寸、无效 ID、重复 YAML 键、归一化后重复 ID、缺失坐标或旋转角，以及非有限数值。

## 三页面工作流

主界面顶部固定为两行：第一行同时放置三个页面标签、当前项目、标定状态和总进度；
第二行是当前页面的操作工具栏，右侧只显示简短状态。详细说明放在工具提示、
“准备 n/4”菜单或页面左侧栏中，不再额外占用独立描述行。

### 1. AprilTag 标定

1. 在顶部项目栏选择项目文件夹。程序自动创建 `_calibration` 和 `_background`，并按固定文件名恢复已有内容。
2. 缺少文件时，通过“导入标定照片”“导入相机内参”和“导入 Tag 布局”选择外部文件；程序验证后复制到项目固定位置。图片尺寸必须与内参严格一致。
3. 点击“检测并完成标定”。检测和映射计算在后台一次完成，并自动保存为 `_calibration/plane_calibration.yaml`。
4. 顶部检查清单显示四个默认标定文件的就绪状态；质量栏显示内点数量、像素/毫米 RMS 及对应阈值。左侧检查反投影的绿色有效区，右侧检查同一有效区的真实平面坐标。默认不显示密集的 Tag ID 和角点编号，可用“显示 Tag 角点详情”打开。
5. 标定可用后，第二个页面自动启用。下次只需选择同一项目，四个默认标定文件会按依赖顺序自动加载。

导入新的照片、内参或布局会使当前平面标定失效；程序确认后将旧标定移入 `_calibration/_history`。导入外部平面标定时，必须先有匹配的标定照片和 Tag 布局。项目中的 `results.json` 仍按标定 ID 检查，不能和不一致的新标定混用。

默认至少需要 2 个已知 Tag 和 8 个最终内点。Tag 应尽量覆盖测量区域，避免集中在一角。未知 ID 只显示、不参与拟合；重复已知 ID 会使标定失败。

### 2. 图片选点

选择一个项目根目录，程序每次都按当前文件夹内容扫描，不生成 ID 清单。飞镖按
`dart_1`、`dart_2` 依次累积；`_background` 直接保存几张正常情况下的公共背景：

```text
dart_project/
├── _background/
│   ├── 1.png
│   ├── 2.png
│   └── 3.png
├── _calibration/
│   ├── camera_intrinsics.yaml
│   ├── tag_layout.yaml
│   ├── calibration_image.png
│   └── plane_calibration.yaml
├── _processed/                 # 程序自动生成
│   ├── active.json
│   └── runs/
├── dart_1/
│   ├── 1.png
│   ├── 2.png
│   └── 3.png
├── dart_2/
│   ├── 1.png
│   └── 2.png
└── results.json
```

一级子文件夹名直接作为飞镖 ID；图片文件名主体必须是大于零的纯 ASCII 整数，并按数值排序。支持 PNG、JPEG、BMP 和 TIFF。同一飞镖下不能用不同扩展名重复同一序号；隐藏目录、下划线开头目录、非数字图片名和其他文件会被忽略。

工具栏“批量增强轨迹”会用 `_background` 中的所有图片生成中位数背景，并处理全部飞镖图片。每次处理都写入新的 `_processed/runs/run_...`，以前的结果不会被覆盖；`_processed/active.json` 指向最新一次至少有一张成功结果的运行。选点页默认显示最新增强图，也可以关闭“显示增强图”切回原始长曝光图。增强图与原图尺寸及像素位置严格一致，选点结果仍引用原始图片坐标。

也可以在项目根目录外批量运行：

```bash
uv run --no-sync python -m plane_picker.trajectory_cli /path/to/dart_project
```

重新执行会产生新的运行目录。可使用 `--gain`、`--noise-multiplier` 和
`--minimum-area` 调整增强倍数、噪声阈值和最小连通区域。

页面左上显示当前图片、飞镖目录和完成进度，左下以紧凑平面图预览当前飞镖的所有已完成落点，右侧大图用于精确选点。平面小图为只读预览：灰色小方框表示 Tag 位置，红色表示当前点，蓝色表示该飞镖的其他次数。每张图片只保存一个点；再次点击右侧图片会移动当前点。工具栏右侧紧凑显示进度、原图/增强图和自动保存状态。`Ctrl+Z` 撤销，`Delete` 删除，`F` 适配视图。“刷新文件夹”会按磁盘现状重新扫描，新增、删除或改名直接按新结构解释。

### 3. 散布结果

第三页为左右结构。左侧勾选框控制飞镖是否显示，选中行决定当前编辑对象；右侧在等比例
X/Y 平面中显示落点和标定有效区。每个选点以圆形落点范围显示，默认半径为
10 mm，可在页面中调整。

选中一个飞镖后，可使用“框选当前飞镖散布范围”在右侧拖出矩形，`Esc` 取消框选。页面和
`results.json` 会记录该矩形的长、宽、中心和方向；每个飞镖最多保存一个手动
散布范围。

如果侧向相机的标定坐标与实际发射方向不一致，可在左侧设置“结果旋转”，也可用 `-90°/0°/+90°/180°` 快捷按钮。
角度以逆时针为正，同时作用于落点、有效平面和手动矩形；它只改变结果坐标的
表达，不会改写原始像素、平面标定或 Tag 布局。

所有 H 对应点首先经过 `cv2.undistortPoints(..., P=K)`，仍使用像素单位。视口缩放、滚动和居中由独立 `CoordinateTransform` 处理。缺少内参时禁止标定。

### RANSAC 阈值的单位

H 方向始终为 `undistorted_pixels_to_plane_mm`。`cv2.findHomography` 的 RANSAC 阈值采用**目标坐标单位**，因此这里传入 `ransac_threshold_mm`，当前配置为 8 mm。另保存 `ransac_threshold_px`，当前配置为 4 px，用于逆向平面→图像重投影的额外筛选。该配置面向约 10 mm 的业务容差，但阈值本身不是精度保证；最终精度仍应使用独立实物点验证。两者在应用配置中独立设置，不能视为同一个数值或固定比例换算。

程序检查 H 有限性、条件数、投影分母、非退化对应点、正反向误差、有效区域和投影地平线。有效区域是**保留内点的平面凸包**，不允许外推。加载标定时再次检查对应点、内点掩码、误差记录、凸包、图像尺寸、相机 ID 和布局哈希。误差统计是内点拟合误差，不能代替独立验证。

改变标定照片、内参或布局会使当前映射失效，并清除界面中的测量批次。标定照片与所有测量图片之间，相机位置、焦距、对焦和分辨率必须保持不变；软件无法仅凭相同图片尺寸证明相机没有移动。

## results.json

首次选点后，程序在测量根目录写入 `results.json`；添加、移动、撤销或删除点时都会原子更新，也可以按 `Ctrl+S` 手动保存。JSON 只保存当前目录规则无法表达的结果数据：

- 标定 ID；
- 飞镖文件夹名；
- 数字图片序号；
- 相对图片路径；
- 原始像素、去畸变像素和平面毫米坐标；
- 选点时间。
- 结果坐标旋转角和落点圆半径；
- 每个飞镖手动框选的矩形散布范围。

重新打开根目录时，目录仍然是飞镖和图片次数的唯一来源。JSON 中已经不存在的图片结果会被忽略；新增图片显示为待选点；JSON 标定 ID 与当前标定不一致时拒绝恢复。

标定 YAML 仍包含相机 ID、图片尺寸、K/D、Tag family、布局哈希、H、有效凸包、误差阈值、内点数和双向 RMS。

## 独立验证点

在平面上布置**独立于拟合 Tag 角点**的已知实测点，制作：

```csv
point_id,x_mm,y_mm
V01,100.0,100.0
V02,250.0,100.0
V03,100.0,250.0
```

独立验证不放在三页面主界面中；需要时使用下面的离线命令行工具。

命令行支持已采集的独立点击 CSV（列 `point_id,u_raw,v_raw`）：

```bash
uv run --no-sync python scripts/validate_mapping.py \
    --calibration plane_calibration.yaml \
    --points validation_points.csv --clicks validation_clicks.csv \
    --output validation_report
```

两张表的 ID 必须一一对应；像素是原图坐标，命令行自动去畸变并检查有效区域。输出 `validation_report.csv` 和 `.json`。二维误差为 `sqrt(error_x_mm² + error_y_mm²)`，汇总的 RMS 为二维误差平方的均值开方。

## 测试与启动检查

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync pytest -q
QT_QPA_PLATFORM=offscreen uv run --no-sync plane-picker --smoke-test
```

关闭 pytest 外部插件自动加载是为了隔离终端中的其他 Python 项目环境。本项目不依赖这些插件。测试默认使用 Qt offscreen，不需要显示器；`--smoke-test` 不创建全局 ID 数据库。

测试覆盖布局和角点顺序、真实 AprilTag 3 四种朝向、外部内参加载、畸变往返、已知合成矩阵恢复、25 Tag / 100 角点拟合、异常点和无效 H、有效区/分母检查、显示坐标与 DPI、Session 与原图恢复、ID 持久化、可撤销编辑、独立验证统计、真实 Qt 鼠标缩放/拖动/去畸变点选和后台线程。精确几何对应点测试要求恢复误差小于 0.001 mm；包含栅格图像和真实检测器的测试允许亚像素检测带来的误差，两类测试分别解释。

## 模块与扩展边界

```text
src/plane_picker/
  camera/        本地图像文件源
  calibration/   CameraModel、TagLayout、TagDetector、PlaneMapper、MappingValidator
  models/        PlaneCalibration、ShotRecord、Session
  storage/       严格 YAML、原子 Session 保存、CSV、持久 Shot ID
  service.py     业务状态与可撤销编辑；UI 只调用业务层
  ui/            双视图、坐标转换、Shot 表格、后台任务和主窗口
  validation_cli.py / demo.py
```

每个 `PickerService` 对应一个 camera_id，拥有独立 K、D、H。文件源返回未经校正的 BGR 图像；检测后端必须遵守 `TagDetector` 的角点契约。未来双目、轨迹或点云模块可以消费 ShotRecord 和带 camera_id 的标定结果。

## 常见问题与当前局限

- **Qt xcb 无法加载**：确认已安装上面的系统库；在正常桌面终端运行。服务器验证用 `QT_QPA_PLATFORM=offscreen`。若终端已有其他 Qt 环境设置，可检查并清除冲突的 `QT_PLUGIN_PATH` / `QT_QPA_PLATFORM_PLUGIN_PATH`；不要混装多个 OpenCV wheel。
- **中文方框**：安装 `fonts-noto-cjk` 后重新启动程序。
- **未检测到 Tag**：检查 family、完整外边界、尺寸、清晰度、曝光、反光、遮挡和实际编码方向；检测不依据红蓝颜色。
- **标定失败或误差大**：检查实测检测边长、中心坐标、逆时针旋转角、镜头内参、输入分辨率和重复 ID。不要仅为通过检查而不断扩大误差阈值。
- **区域外不能点选**：有效区只覆盖内点凸包。增加分布更广的 Tag，重新标定；不允许靠一个小 Tag 外推大区域。单 Tag 仅在计算核心显式调试参数下可用，桌面流程始终要求至少两个。
- **Session 图片找不到**：Session 不嵌入图像。恢复原路径，或在 JSON 中同时更新 Session 和各 Shot 的 `image_file`。标定与布局快照不可随意修改。
- **同色编号不连续**：全局 ID 包含两种颜色和已撤销记录；这是有意保留的唯一身份。需要连续展示时使用“重排显示编号”。
- 当前只支持 OpenCV 针孔畸变模型，不把鱼眼参数自动解释为普通 distortion。
- 平面标定不能校正非平面表面、Tag 安装不共面、相机移动、运动模糊或空气中目标的高度误差。
- 自动测试和演示证明的是计算与交互链路。实际精度仍需使用真实内参、实测 Tag 尺寸/布局以及独立实测验证点确认；没有宣称达到厘米级。
