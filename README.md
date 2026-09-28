# AprilTag 平面点选定位工具

桌面工具，目标平台为 Ubuntu 24.04 / Python 3.12。程序从本地文件加载原始图像和相机内参；随后检测 AprilTag，建立**去畸变像素 → Z=0 平面毫米坐标**的映射，并人工点选红、蓝飞镖位置。支持双视图、Shot 编辑与撤销、Session 恢复、CSV/JSON 导出和独立验证点误差报告。

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

## 先运行合成演示

仓库提供 `examples/synthetic/`：四个不同朝向的真实 AprilTag 编码、三个红蓝点、布局、调试内参、平面标定和 Session。启动界面后选择“加载 Session”，打开 `examples/synthetic/session.json`，即可查看左右标记和表格。

演示的相机内参明确标记为 `intrinsics_valid: false`，界面显示调试警告。演示验证报告只验证软件流程，不是实测精度报告。已有示例文件包含生成时的绝对路径；如果复制了项目到另一台机器，请生成新的演示目录：

```bash
uv run --no-sync python -m plane_picker.demo /tmp/plane-picker-demo
```

生成器拒绝覆盖已有演示文件。也可以只打开演示图像，依次加载同目录下的相机参数和布局，点击“检测 AprilTag”“计算映射”，从头走一遍流程。

## 加载外部标定的相机内参

相机内参由外部程序完成标定，本项目不提供内参标定工具。内参 YAML 格式参考
`config/calibration/00DA1923282.yaml`，然后通过界面“相机内参”加载。程序保留
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
ID 按照片从上到下为 `7–11、22–26、37–41、52–56、67–71`。照片中的所有 Tag 相对其
编码正向旋转了 180°；布局坐标系已经随整块板旋转 180°，因此以 Tag 正确朝向观察时，
所有条目的 `rotation_deg` 都是 `0`。

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

## 平面标定与点选

1. 打开原始相机图像，加载真实内参和实测 Tag 布局。图像宽高必须与内参严格一致；不自动缩放或裁剪内参。
2. 点击“检测 AprilTag”。检测使用原始灰度图，在后台执行；显示 Tag ID 和 0–3 号角点。未知 ID 可以显示，但不会参与拟合。重复已知 ID 会使标定失败。
3. 点击“计算映射”。默认至少 2 个已知 Tag，且至少 8 个最终内点。请让 Tag 分布覆盖整个测量区域，避免集中在一角。
4. 查看绿色内点、橙色外点、角点数量、像素重投影 RMS 和毫米映射 RMS。点击“保存平面标定”，默认文件名 `plane_calibration.yaml`。
5. 选择红色或蓝色，在左图有效区域内点击。左右视图和 Shot 表格立即同步；区域外、图像外或投影分母异常时不会创建记录。
6. 滚轮缩放；空白处拖拽或用中键/右键拖拽平移；“适配两视图”或 `F` 适配内容。“去畸变显示”切换原图和保持同一 K 的校正图。

所有 H 的对应点首先经过 `cv2.undistortPoints(..., P=K)`，仍使用像素单位。原图点击也走同一条路径；去畸变视图点击先转换回原始像素，以便保存两套一致坐标。视口缩放、滚动、居中、黑边由独立 `CoordinateTransform` 处理。Qt 鼠标事件已经是逻辑像素，不能再乘除一次 DPI；物理屏幕像素输入有独立的 DPR 转换入口。

缺少内参时禁止标定。只有显式启用“零畸变调试”或加载标记为无效的示例内参时，才允许调试拟合；警告一直显示，`intrinsics_valid: false` 写入标定和 Session。

### RANSAC 阈值的单位

H 方向始终为 `undistorted_pixels_to_plane_mm`。`cv2.findHomography` 的 RANSAC 阈值采用**目标坐标单位**，因此这里传入 `ransac_threshold_mm`，默认 3 mm。为兼容需求中的像素质量阈值，另保存 `ransac_threshold_px`，默认 3 px，用于逆向平面→图像重投影的额外筛选。两者在应用配置中独立设置，不能视为同一个数值或固定比例换算。

程序检查 H 有限性、条件数、投影分母、非退化对应点、正反向误差、有效区域和投影地平线。有效区域是**保留内点的平面凸包**，不允许外推。加载标定时再次检查对应点、内点掩码、误差记录、凸包、图像尺寸、相机 ID 和布局哈希。误差统计是内点拟合误差，不能代替独立验证。

改变图像、内参、布局或调试模式会使当前映射失效。已有 Shot 时，先保存并“新建本轮”，再更换输入。加载已有 H 只能用于相机位置、焦距、对焦、分辨率和 Tag 平面均未改变的场景；软件无法仅凭相同图像尺寸证明相机未移动。

## Shot 编辑、Session 和导出

- 点选标记或表格行选择 Shot。拖动左图或右侧平面的标记可调整位置；“编辑 / 微调”可以按原始像素以 0.1 px 步长修改位置或颜色。
- `Delete` 删除选中点；`Ctrl+Z` 撤销添加、移动、改色、删除、清空和重排标签。撤销不会回退 ID 分配器；取消的 ID 不再复用。
- `shot_id` 与颜色无关。桌面程序在用户应用数据目录（通常 `~/.local/share/plane_picker/shot_ids.sqlite`）保存事务化递增序列，跨本轮、重启和同用户多实例持续递增。加载 Session 会提升序列下界。不同机器独立生成的记录用 `(session_id, shot_id)` 联合识别。
- “重排显示编号”按颜色重排 R01/R02/B01 等 `shot_label`，**不改变稳定的 shot_id**。初次添加的显示标签使用全局 ID，因此出现 R01、B02、R03 是正常的。
- “清空本轮”可撤销；“新建本轮”产生新的 session_id、保留当前图像和标定并清除撤销历史。关闭或替换未保存本轮时会提醒。
- 保存 Session（`Ctrl+S`）需要已完成标定。JSON 保存路径、所有 Shot、下一 ID、版本、标定和布局的完整快照。恢复以快照为准，不依赖外部标定 YAML 仍保持原样；原始图像文件仍须存在。
- Session 加载先验证所有依赖与 Shot 坐标，再替换当前本轮；失败不会清掉当前数据。相对图像路径按 Session 文件所在目录解析。
- CSV 含需求中的 13 个字段；JSON 导出包含完整 Session 信息；UI 截图导出 PNG。

标定 YAML 包含相机 ID、原图尺寸、K/D、内参有效性和版本、Tag family、布局路径/哈希、参与 Tag ID、H、有效凸包、两个阈值、内点数、双向 RMS、日期和校验所需的所有对应点/掩码。

## 独立验证点

在平面上布置**独立于拟合 Tag 角点**的已知实测点，制作：

```csv
point_id,x_mm,y_mm
V01,100.0,100.0
V02,250.0,100.0
V03,100.0,250.0
```

点击“加载独立验证点”，按横幅顺序在左图点击对应位置。此时点击记录为验证样本，不产生 Shot；“撤销验证点”可以重选上一点。“结束验证”返回 Shot 点选。验证结果显示每点 X/Y 偏差、二维误差，以及平均值、RMS、中位数、P95、最大值；完成后弹窗展示，横幅保留 RMS/P95。点击“导出验证报告”同时生成 CSV 和 JSON，未完成报告在 JSON 中标为 `complete: false`。当前验证结果不写入 Shot Session，请单独导出后再更换标定或关闭。

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
