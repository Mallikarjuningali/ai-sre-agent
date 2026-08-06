"""
=========================================================
AI SRE AGENT

Main Entry Point

Execution Flow

1. Collect AWS Infrastructure Data
2. Build AI Context
3. Run AI Investigation
4. Generate Reports
5. Export Dashboard Feed

Author : Mallikarjun
=========================================================
"""

from collector.cloudwatch import main as cloudwatch_collector
from collector.alb import main as alb_collector
from collector.autoscaling import main as autoscaling_collector
from collector.cloudtrail import main as cloudtrail_collector

from analyzer.analyzer import Analyzer

from utils.logger import get_logger

logger = get_logger("Main")


def run_collectors():

    logger.info("====================================================")
    logger.info("Running AWS Collectors")
    logger.info("====================================================")

    cloudwatch_collector()

    alb_collector()

    autoscaling_collector()

    cloudtrail_collector()

    logger.info("====================================================")
    logger.info("Collectors Completed Successfully")
    logger.info("====================================================")


def main():

    logger.info("====================================================")
    logger.info("AI SRE Agent Started")
    logger.info("====================================================")

    # Step 1
    run_collectors()

    # Step 2
    analyzer = Analyzer()
    analyzer.run_all()

    logger.info("====================================================")
    logger.info("AI SRE Agent Completed Successfully")
    logger.info("====================================================")


if __name__ == "__main__":
    main()
