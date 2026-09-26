import csv
import io
from .yaml_io import atomic_text


def export_csv(path, session):
    fields = "session_id,shot_id,shot_label,color,x_mm,y_mm,u_raw,v_raw,u_undistorted,v_undistorted,image_file,calibration_id".split(",")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for s in session.shots:
        writer.writerow(dict(session_id=session.session_id, shot_id=s.shot_id, shot_label=s.shot_label,
                             color=s.color, x_mm=s.plane_mm[0], y_mm=s.plane_mm[1],
                             u_raw=s.pixel_raw[0], v_raw=s.pixel_raw[1],
                             u_undistorted=s.pixel_undistorted[0], v_undistorted=s.pixel_undistorted[1],
                             image_file=s.image_file, calibration_id=s.calibration_id))
    atomic_text(path, output.getvalue())
