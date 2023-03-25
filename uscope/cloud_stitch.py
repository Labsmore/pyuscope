import os
import io
try:
    import boto3
except ImportError:
    boto3 = None
from uscope import config


def upload_dir(
    directory,
    access_key=None,
    secret_key=None,
    id_key=None,
    notification_email=None,
    verbose=True,
    log=None,
    # threading.Event()
    running=None):

    if boto3 is None:
        raise ImportError("Requires boto3")

    bc = config.get_bc()

    if not access_key:
        access_key = bc.labsmore_stitch_aws_access_key()
    if not access_key:
        raise ValueError("Requires access_key")

    if not secret_key:
        secret_key = bc.labsmore_stitch_aws_secret_key()
    if not secret_key:
        raise ValueError("Requires secret_key")

    if not id_key:
        id_key = bc.labsmore_stitch_aws_id_key()
    if not id_key:
        raise ValueError("Requires id_key")

    if not notification_email:
        notification_email = bc.labsmore_stitch_notification_email()
    if not notification_email:
        raise ValueError("Requires notification_email")

    if log is None:

        def log(s):
            print(s)

    log(f"CloudStitch uploading: {directory}")

    S3BUCKET = 'labsmore-mosaic-service'
    DEST_DIR = id_key + '/' + os.path.basename(os.path.abspath(directory))
    s3 = boto3.client('s3',
                      aws_access_key_id=access_key,
                      aws_secret_access_key=secret_key)

    for root, _, files in os.walk(directory):
        for file in sorted(files):
            if running is not None and not running.is_set():
                raise Exception("Upload interrupted")
            verbose and log('Uploading {} to {}/{} '.format(
                os.path.join(root, file), S3BUCKET, DEST_DIR + '/' + file))
            s3.upload_file(os.path.join(root, file), S3BUCKET,
                           DEST_DIR + '/' + file)

    MOSAIC_RUN_CONTENT = u'{{ "email": "{}" }}'.format(notification_email)
    mosaic_run_json = io.BytesIO(bytes(MOSAIC_RUN_CONTENT, encoding='utf8'))
    s3.upload_fileobj(mosaic_run_json, S3BUCKET,
                      DEST_DIR + '/' + 'mosaic_run.json')

    log("CloudStitch uploaded")
