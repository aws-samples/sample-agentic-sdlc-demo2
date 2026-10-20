import * as s3 from "aws-cdk-lib/aws-s3";
import { Construct } from "constructs";

/**
 * Thin wrapper around an externally-managed S3 bucket that holds the zipped
 * deployment assets (agent source, UI build, Lambda code, isv-agents.json).
 *
 * The bucket is created and populated by `scripts/package-assets.sh` *before*
 * `cdk deploy` runs. The bucket name is passed in via CDK context
 * (`-c assetsBucketName=...`) and imported here with `fromBucketName`.
 *
 * Why not a CDK-managed bucket + BucketDeployment? A runtime BucketDeployment
 * custom resource requires a Lambda role with write access to the bucket,
 * which shares the same bucket policy that other consumers grant read from.
 * CloudFormation then can't break the ordering cycle. Uploading at package
 * time avoids that problem entirely — from CloudFormation's perspective, the
 * bucket already exists with the objects in place.
 */
export class AssetsBucket extends Construct {
  public readonly bucket: s3.IBucket;
  public readonly keyPrefix: string;

  constructor(scope: Construct, id: string, bucketName: string) {
    super(scope, id);

    this.keyPrefix = "assets";
    this.bucket = s3.Bucket.fromBucketName(this, "AssetBucket", bucketName);
  }

  /**
   * Returns the S3 key for a named asset placed under `./assets/`
   * by the packaging script.
   */
  public getS3Key(name: string): string {
    return `${this.keyPrefix}/${name}`;
  }
}
